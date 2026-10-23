import logging
import threading

import requests

import degiro_connector.core.constants.headers as default_headers

# Hardened fork: a request sent without a timeout gets this (connect, read)
# budget instead of waiting forever. Callers with a tighter budget pass their
# own.
DEFAULT_TIMEOUT = (10, 60)


class _BoundedSession(requests.Session):
    def send(self, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = DEFAULT_TIMEOUT
        return super().send(request, **kwargs)


class ModelSession:
    """Handle the Requests Session objects in a threadsafe manner."""

    @staticmethod
    def build_session(
        headers: dict | None = None,
        hooks: dict | None = None,
    ) -> requests.Session:
        """Setup a "requests.Session" object.
        Args:
            headers (dict, optional):
                Headers to used for the Session.
                Defaults to None.
            hooks (dict, optional):
                Hooks for the Session.
                Defaults to None.

        Returns:
            requests.Session:
                Session object with the right headers and hooks.
        """

        session = _BoundedSession()
        # Hardened fork: never pick up proxies, netrc or CA overrides from the
        # environment.
        session.trust_env = False

        if isinstance(headers, dict):
            session.headers.update(headers)
        else:
            session.headers.update(default_headers.HEADERS)

        if isinstance(hooks, dict):
            session.hooks.update(hooks)

        return session

    @property
    def session(self) -> requests.Session:
        self.__logger.debug("session:getter: %s", threading.current_thread().name)

        if not hasattr(self.__local_storage, "session"):
            self.__local_storage.session = self.build_session(
                headers=self.__headers,
                hooks=self.__hooks,
            )

        return self.__local_storage.session

    @session.setter
    def session(self, session: requests.Session):
        self.__logger.debug("session:setter: %s", threading.current_thread().name)

        self.__local_storage.session = session

    def reset_session(
        self,
        headers: dict | None = None,
        hooks: dict | None = None,
    ):
        self.__local_storage.session = self.build_session(
            headers=headers,
            hooks=hooks,
        )

    def __init__(
        self,
        headers: dict | None = None,
        hooks: dict | None = None,
    ):
        self.__logger = logging.getLogger(self.__module__)
        self.__local_storage = threading.local()

        if isinstance(headers, dict):
            headers = dict(headers)

        if isinstance(hooks, dict):
            hooks = dict(hooks)

        self.__headers = headers
        self.__hooks = hooks
