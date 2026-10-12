import http.client
from typing import Literal

from brace._kubernetes_responsiveness import (
    _HttpRequest,
    _HttpResponse,
    _TransportError,
)


class _HttpClientTransport:
    def get(
        self,
        request: _HttpRequest,
        *,
        follow_redirects: Literal[False],
    ) -> _HttpResponse:
        connection: http.client.HTTPConnection | None = None
        host = f"{request.target.service_name}.{request.target.namespace}.svc"

        try:
            connection = http.client.HTTPConnection(
                host,
                request.target.port,
                timeout=request.timeout,
            )
            connection.request("GET", "/")
            response = connection.getresponse()
            return _HttpResponse(status_code=response.status)
        except TimeoutError as e:
            raise _TransportError("request timed out") from e
        except (ConnectionError, OSError) as e:
            raise _TransportError("connection failed") from e
        except http.client.HTTPException as e:
            raise _TransportError("HTTP protocol failure") from e
        finally:
            if connection is not None:
                connection.close()
