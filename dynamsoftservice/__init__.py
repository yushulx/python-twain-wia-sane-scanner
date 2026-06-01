import json
import os
import time
import requests
from typing import List, Any, Dict, Optional


DEFAULT_TIMEOUT = 30
DEFAULT_IMAGE_TYPE = 'image/png'
DEFAULT_DOCUMENT_TYPE = 'application/pdf'
DEFAULT_CHUNK_SIZE = 8192


class ScannerType:
    TWAINSCANNER = 0x10
    WIASCANNER = 0x20
    TWAINX64SCANNER = 0x40
    ICASCANNER = 0x80
    SANESCANNER = 0x100
    ESCLSCANNER = 0x200
    WIFIDIRECTSCANNER = 0x400
    WIATWAINSCANNER = 0x800

class JobStatus:
    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAULTED = 'faulted'
    CANCELED = 'canceled'


class ScannerServiceError(RuntimeError):
    """Raised when the Dynamic Web TWAIN Service returns an error response."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details or {}

class ScannerController:
    """
    A class that provides methods to interact with the Dynamic Web TWAIN Service API.
    """

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        verify: bool = True,
        session: Optional[requests.Session] = None,
        raise_errors: bool = False,
    ) -> None:
        self.timeout = timeout
        self.verify = verify
        self.raise_errors = raise_errors
        self.session = session or requests.Session()
        self.last_error: Optional[Dict[str, Any]] = None

    def __enter__(self) -> 'ScannerController':
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()

    def _build_url(self, host: str, path: str) -> str:
        base_url = host.rstrip('/')
        api_path = path.lstrip('/')
        if base_url.endswith('/api'):
            return f"{base_url}/{api_path}"
        return f"{base_url}/api/{api_path}"

    def _make_headers(
        self,
        product_key: str = '',
        document_password: str = '',
        content_type: Optional[str] = 'application/json',
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if content_type:
            headers['Content-Type'] = content_type
        if product_key:
            headers['DWT-PRODUCT-KEY'] = product_key
        if document_password:
            headers['DWT-DOC-PASSWORD'] = document_password
        return headers

    def _clean_params(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cleaned: Dict[str, Any] = {}
        if not params:
            return cleaned

        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                cleaned[key] = ','.join(str(item) for item in value)
            elif isinstance(value, bool):
                cleaned[key] = str(value).lower()
            else:
                cleaned[key] = value
        return cleaned

    def _send_request(
        self,
        method: str,
        host: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        stream: bool = False,
        timeout: Optional[int] = None,
        absolute_url: Optional[str] = None,
    ) -> Optional[requests.Response]:
        request_url = absolute_url or self._build_url(host, path)
        try:
            response = self.session.request(
                method=method,
                url=request_url,
                headers=headers,
                params=self._clean_params(params),
                json=payload,
                timeout=timeout or self.timeout,
                verify=self.verify,
                stream=stream,
            )
            self.last_error = None
            return response
        except requests.RequestException as error:
            payload = {'error': str(error)}
            self.last_error = payload
            if self.raise_errors:
                raise ScannerServiceError(str(error), details=payload)
        return None

    def _handle_response_error(self, response: requests.Response) -> Dict[str, Any]:
        error_payload: Dict[str, Any] = {
            'statusCode': response.status_code,
            'message': response.reason or 'Request failed.',
        }
        try:
            data = response.json()
            if isinstance(data, dict):
                error_payload.update(data)
            else:
                error_payload['error'] = data
        except ValueError:
            if response.text:
                error_payload['error'] = response.text
                error_payload['message'] = response.text

        self.last_error = error_payload
        if self.raise_errors:
            raise ScannerServiceError(
                error_payload.get('message', 'Request failed.'),
                status_code=response.status_code,
                details=error_payload,
            )
        return error_payload

    def _request_json(
        self,
        method: str,
        host: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        fallback: Optional[Any] = None,
        allow_no_content: bool = False,
    ) -> Any:
        response = self._send_request(method, host, path, headers=headers, params=params, payload=payload)
        if response is None:
            return fallback if fallback is not None else self.last_error

        if response.status_code == 204 and allow_no_content:
            self.last_error = None
            return fallback

        if not response.ok:
            error_payload = self._handle_response_error(response)
            return fallback if fallback is not None else error_payload

        try:
            return response.json()
        except ValueError as error:
            error_payload = {
                'statusCode': response.status_code,
                'message': str(error),
                'error': response.text,
            }
            self.last_error = error_payload
            if self.raise_errors:
                raise ScannerServiceError(str(error), status_code=response.status_code, details=error_payload)
            return fallback if fallback is not None else error_payload

    def _request_content(
        self,
        method: str,
        host: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        allow_no_content: bool = False,
        absolute_url: Optional[str] = None,
    ) -> Optional[bytes]:
        response = self._send_request(
            method,
            host,
            path,
            headers=headers,
            params=params,
            payload=payload,
            absolute_url=absolute_url,
        )
        if response is None:
            return None

        if response.status_code == 204 and allow_no_content:
            self.last_error = None
            return None

        if not response.ok:
            self._handle_response_error(response)
            return None
        return response.content

    def _request_success(
        self,
        method: str,
        host: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        payload: Optional[Dict[str, Any]] = None,
        expected_status_codes: Optional[List[int]] = None,
    ) -> bool:
        expected = expected_status_codes or [200, 204]
        response = self._send_request(method, host, path, headers=headers, params=params, payload=payload)
        if response is None:
            return False
        if response.status_code in expected:
            self.last_error = None
            return True

        self._handle_response_error(response)
        return False

    def _resolve_extension(self, content_type: str) -> str:
        extension_map = {
            'image/jpeg': '.jpg',
            'image/png': '.png',
            'image/tiff': '.tiff',
            'application/pdf': '.pdf',
        }
        return extension_map.get(content_type, '.bin')

    def _write_content_to_file(
        self,
        content: Optional[bytes],
        directory: str,
        prefix: str,
        content_type: str,
        filename: Optional[str] = None,
    ) -> str:
        if not content:
            return ''

        os.makedirs(directory, exist_ok=True)
        extension = self._resolve_extension(content_type)
        resolved_filename = filename or f"{prefix}_{int(time.time() * 1000)}{extension}"
        path_to_file = os.path.join(directory, resolved_filename)
        with open(path_to_file, 'wb') as output_stream:
            output_stream.write(content)
        return resolved_filename

    def getServerSettings(self, host: str) -> Dict[str, Any]:
        """Get Dynamic Web TWAIN Service runtime settings."""
        response = self._request_json('GET', host, 'server', fallback={})
        return response if isinstance(response, dict) else {}

    def updateServerSettings(self, host: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Update Dynamic Web TWAIN Service runtime settings."""
        response = self._request_json(
            'PATCH',
            host,
            'server',
            headers=self._make_headers(),
            payload=parameters,
            fallback={},
        )
        return response if isinstance(response, dict) else {}

    def getServerInfo(self, host: str) -> Dict[str, Any]:
        """Get version info of the TWAIN server."""
        response = self._request_json('GET', host, 'server/version', fallback={"version": "", "compatible": False})
        return response if isinstance(response, dict) else {"version": "", "compatible": False}

    def getDevices(self, host: str, scannerType: Optional[int] = None) -> List[Any]:
        """Get a list of available scanners."""
        params = {'type': scannerType} if scannerType is not None else None
        response = self._request_json('GET', host, 'device/scanners', params=params, fallback=[])
        return response if isinstance(response, list) else []

    def createJob(self, host: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new scan job."""
        payload = dict(parameters)
        product_key = payload.pop('license', '')
        response = self._request_json(
            'POST',
            host,
            'device/scanners/jobs',
            headers=self._make_headers(product_key=product_key),
            payload=payload,
            fallback={},
        )
        return response if isinstance(response, dict) else {}

    def deleteJob(self, host: str, jobId: str) -> bool:
        """Delete a scan job."""
        if not jobId:
            return False
        return self._request_success('DELETE', host, f'device/scanners/jobs/{jobId}', expected_status_codes=[204])

    def updateJob(self, host: str, jobId: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Update scan job status (e.g., 'running', 'canceled')."""
        response = self._request_json(
            'PATCH',
            host,
            f'device/scanners/jobs/{jobId}',
            headers=self._make_headers(),
            payload=parameters,
            fallback={},
        )
        return response if isinstance(response, dict) else {}

    def checkJob(self, host: str, jobId: str) -> Dict[str, Any]:
        """Check the status of an existing scan job."""
        response = self._request_json('GET', host, f'device/scanners/jobs/{jobId}', fallback={})
        return response if isinstance(response, dict) else {}

    def getImageStream(self, host: str, jobId: str, imageType: str = DEFAULT_IMAGE_TYPE) -> Optional[bytes]:
        """Get the next scanned image as a byte stream."""
        return self._request_content(
            'GET',
            host,
            f'device/scanners/jobs/{jobId}/next-page',
            params={'type': imageType},
            allow_no_content=True,
        )

    def getImageFile(
        self,
        host: str,
        jobId: str,
        directory: str,
        imageType: str = DEFAULT_IMAGE_TYPE,
        filename: Optional[str] = None,
    ) -> str:
        """Download a single scanned image and save it to disk."""
        content = self.getImageStream(host, jobId, imageType=imageType)
        return self._write_content_to_file(content, directory, 'image', imageType, filename=filename)

    def getImageFiles(
        self,
        host: str,
        jobId: str,
        directory: str,
        imageType: str = DEFAULT_IMAGE_TYPE,
    ) -> List[str]:
        """Download all scanned images of a job as files."""
        images = []
        while True:
            filename = self.getImageFile(host, jobId, directory, imageType=imageType)
            if not filename:
                break
            images.append(filename)
        return images

    def getImageStreams(self, host: str, jobId: str, imageType: str = DEFAULT_IMAGE_TYPE) -> List[bytes]:
        """Get all scanned images as byte streams."""
        streams = []
        while True:
            image_stream = self.getImageStream(host, jobId, imageType=imageType)
            if image_stream is None:
                break
            streams.append(image_stream)
        return streams

    def getImageInfo(self, host: str, jobId: str) -> Dict[str, Any]:
        """Get information of the next scanned page."""
        response = self._request_json(
            'GET',
            host,
            f'device/scanners/jobs/{jobId}/next-page-info',
            fallback={},
            allow_no_content=True,
        )
        if isinstance(response, list):
            return response[0] if response else {}
        return response if isinstance(response, dict) else {}

    def getScannerCapabilities(self, host: str, jobId: str, caps: Optional[List[int]] = None) -> Any:
        """Get scanner capabilities (e.g., DPI, color mode)."""
        params = {'caps': caps} if caps else None
        return self._request_json(
            'GET',
            host,
            f'device/scanners/jobs/{jobId}/scanner/capabilities',
            params=params,
            fallback=[],
        )

    def getScannerSettings(self, host: str, jobId: str, showUI: bool = True) -> Dict[str, Any]:
        """Retrieve TWAIN settings for a pending scan job."""
        response = self._request_json(
            'GET',
            host,
            f'device/scanners/jobs/{jobId}/scanner/settings',
            params={'showui': showUI},
            fallback={},
        )
        return response if isinstance(response, dict) else {}

    ##################
    # Document-related
    ##################

    def createDocument(self, host: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new document."""
        response = self._request_json(
            'POST',
            host,
            'storage/documents',
            headers=self._make_headers(),
            payload=parameters,
            fallback={},
        )
        return response if isinstance(response, dict) else {}

    def getDocumentInfo(self, host: str, docId: str, password: str = '') -> Dict[str, Any]:
        """Get document metadata."""
        response = self._request_json(
            'GET',
            host,
            f'storage/documents/{docId}',
            headers=self._make_headers(content_type=None, document_password=password),
            fallback={},
        )
        return response if isinstance(response, dict) else {}

    def deleteDocument(self, host: str, docId: str, password: str = '') -> bool:
        """Delete an existing document."""
        return self._request_success(
            'DELETE',
            host,
            f'storage/documents/{docId}',
            headers=self._make_headers(content_type=None, document_password=password),
            expected_status_codes=[204],
        )

    def getDocumentFile(
        self,
        host: str,
        docId: str,
        directory: str,
        parameters: Optional[Dict[str, Any]] = None,
        documentPassword: str = '',
        filename: Optional[str] = None,
    ) -> str:
        """Download a document (PDF) and save it to disk."""
        request_parameters = dict(parameters or {})
        output_type = request_parameters.get('type', DEFAULT_DOCUMENT_TYPE)
        content = self.getDocumentStream(host, docId, parameters=request_parameters, documentPassword=documentPassword)
        return self._write_content_to_file(content, directory, 'document', output_type, filename=filename)

    def getDocumentStream(
        self,
        host: str,
        docId: str,
        parameters: Optional[Dict[str, Any]] = None,
        documentPassword: str = '',
    ) -> Optional[bytes]:
        """Get document content as byte stream."""
        return self._request_content(
            'GET',
            host,
            f'storage/documents/{docId}/content',
            headers=self._make_headers(content_type=None, document_password=documentPassword),
            params=parameters,
        )

    def insertPage(self, host: str, docId: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new page into an existing document."""
        payload = dict(parameters)
        document_password = payload.pop('password', '')
        response = self._request_json(
            'POST',
            host,
            f'storage/documents/{docId}/pages',
            headers=self._make_headers(document_password=document_password),
            payload=payload,
            fallback={},
        )
        return response if isinstance(response, dict) else {}

    def deletePage(self, host: str, docId: str, pageId: str, password: str = '') -> bool:
        """Delete a page from a document."""
        return self._request_success(
            'DELETE',
            host,
            f'storage/documents/{docId}/pages/{pageId}',
            headers=self._make_headers(content_type=None, document_password=password),
            expected_status_codes=[204],
        )

    ##################
    # Processing-related
    ##################

    def readBarcode(self, host: str, parameters: Dict[str, Any]) -> Any:
        """Read barcodes from a scanned page source URL."""
        payload = dict(parameters)
        product_key = payload.pop('license', '')
        return self._request_json(
            'POST',
            host,
            'process/read-barcode',
            headers=self._make_headers(product_key=product_key),
            payload=payload,
            fallback=[],
        )

    def checkBlank(self, host: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Check whether a scanned page source URL is blank."""
        response = self._request_json(
            'POST',
            host,
            'process/check-blank',
            headers=self._make_headers(),
            payload=parameters,
            fallback={},
        )
        return response if isinstance(response, dict) else {}

    def getStreamFromUrl(self, url: str) -> Optional[bytes]:
        """Download binary content from an absolute Dynamic Web TWAIN URL."""
        return self._request_content('GET', '', '', allow_no_content=True, absolute_url=url)


__all__ = [
    'DEFAULT_CHUNK_SIZE',
    'DEFAULT_DOCUMENT_TYPE',
    'DEFAULT_IMAGE_TYPE',
    'DEFAULT_TIMEOUT',
    'JobStatus',
    'ScannerController',
    'ScannerServiceError',
    'ScannerType',
]
