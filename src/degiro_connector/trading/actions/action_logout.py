import logging

import requests

from degiro_connector.core.constants import urls
from degiro_connector.trading.models.credentials import Credentials
from degiro_connector.core.abstracts.abstract_action import AbstractAction


# Hardened fork: logout must not hang the caller's teardown.
LOGOUT_TIMEOUT = (2, 5)


class ActionLogout(AbstractAction):
    @classmethod
    def logout(
        cls,
        credentials: Credentials,
        session_id: str,
        logger: logging.Logger | None = None,
        session: requests.Session | None = None,
    ) -> bool | None:
        if logger is None:
            logger = cls.build_logger()
        if session is None:
            session = cls.build_session()

        int_account = credentials.int_account
        url = urls.LOGOUT
        url = f"{url};jsessionid={session_id}"

        params = {
            "intAccount": int_account,
            "sessionId": session_id,
        }

        request = requests.Request(
            method="PUT",
            url=url,
            params=params,
        )
        prepped = session.prepare_request(request)
        response_raw = None

        try:
            response_raw = session.send(
                prepped,
                allow_redirects=False,
                timeout=LOGOUT_TIMEOUT,
            )
            response_raw.raise_for_status()
        except requests.HTTPError as e:
            logger.fatal("request failed: %s", type(e).__name__)
            status_code = getattr(response_raw, "status_code", "No status_code found.")
            logger.fatal("status_code: %s", status_code)
            return None
        except Exception as e:
            logger.fatal("request failed: %s", type(e).__name__)
            return None

        return response_raw.status_code == 200

    def call(self) -> bool | None:
        connection_storage = self.connection_storage
        session_id = connection_storage.session_id
        session = self.session_storage.session
        logger = self.logger
        credentials = self.credentials

        return self.logout(
            credentials=credentials,
            session_id=session_id,
            logger=logger,
            session=session,
        )
