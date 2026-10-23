from pydantic import BaseModel, Field, field_validator, model_validator

# Hardened fork: the file/env helper `build_credentials` is removed from the
# package. Credentials are constructed explicitly by the caller; this module
# reads no environment variable and no config file. The examples keep a copy
# of the helper in `examples/credentials_from_config.py`.

OTP_LENGTH = 6


class Credentials(BaseModel):
    int_account: int | None = Field(default=None)
    username: str
    password: str = Field(repr=False)
    totp_secret_key: str | None = Field(default=None, repr=False)
    # Hardened fork: a string, so a leading zero survives ("012345").
    one_time_password: str | None = Field(default=None, repr=False, strict=True)
    in_app_token: str | None = Field(default=None, repr=False)

    @field_validator("totp_secret_key", "in_app_token")
    @classmethod
    def empty_is_absent(cls, v: str | None) -> str | None:
        # Login branches on `is not None`; an empty value means "not given".
        return v or None

    @field_validator("one_time_password")
    @classmethod
    def six_ascii_digits(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) != OTP_LENGTH or not (v.isascii() and v.isdigit()):
            raise ValueError("`one_time_password` must be exactly six ASCII digits.")
        return v

    @model_validator(mode="after")
    def one_of(self) -> "Credentials":
        if self.one_time_password is not None and self.totp_secret_key is not None:
            raise ValueError(
                "You can't set both `one_time_password` and `totp_secret_key`."
            )
        return self
