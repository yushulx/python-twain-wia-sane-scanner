import time
import os
import json
import requests
from typing import List, Any, Dict, Optional


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
    RUNNING = 'running'
    CANCELED = 'canceled'

class ScannerController:
    """
    A class that provides methods to interact with the Dynamic Web TWAIN Service API.
    """

    def getServerInfo(self, host: str) -> Dict[str, Any]:
        """Get version info of the TWAIN server."""
        url = f"{host}/api/server/version"
        try:
            response = requests.get(url)
            return response.json()
        except Exception as e:
            return {"version": str(e), "compatible": False}

    def getDevices(self, host: str, scannerType: Optional[int] = None) -> List[Any]:
        """Get a list of available scanners."""
        url = f"{host}/api/device/scanners"
        if scannerType is not None:
            url += f"?type={scannerType}"

        try:
            response = requests.get(url)
            return response.json()
        except Exception:
            return []

    def createJob(self, host: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new scan job."""
        url = f"{host}/api/device/scanners/jobs"
        try:
            headers = {
                'Content-Type': 'application/json',
                'DWT-PRODUCT-KEY': parameters.get("license", ""),
                'Content-Length': str(len(json.dumps(parameters)))
            }
            response = requests.post(url, headers=headers, json=parameters)
            return response.json()
        except Exception as error:
            return {"error": str(error)}

    def deleteJob(self, host: str, jobId: str) -> None:
        """Delete a scan job."""
        if not jobId:
            return
        url = f"{host}/api/device/scanners/jobs/{jobId}"
        try:
            requests.delete(url)
        except Exception:
            pass

    def updateJob(self, host: str, jobId: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Update scan job status (e.g., 'running', 'canceled')."""
        url = f"{host}/api/device/scanners/jobs/{jobId}"
        try:
            headers = {
                'Content-Type': 'application/json',
                'Content-Length': str(len(json.dumps(parameters)))
            }
            response = requests.patch(url, headers=headers, json=parameters)
            return response.json()
        except Exception as error:
            return {"error": str(error)}

    def checkJob(self, host: str, jobId: str) -> Dict[str, Any]:
        """Check the status of an existing scan job."""
        url = f"{host}/api/device/scanners/jobs/{jobId}"
        try:
            response = requests.get(url)
            return response.json()
        except Exception as error:
            return {"error": str(error)}

    def getImageFile(self, host: str, job_id: str, directory: str) -> str:
        """Download a single scanned image and save it to disk."""
        url = f"{host}/api/device/scanners/jobs/{job_id}/next-page"
        try:
            response = requests.get(url, stream=True)
            if response.status_code == 200:
                filename = f"image_{int(time.time() * 1000)}.jpg"
                path_to_file = os.path.join(directory, filename)
                with open(path_to_file, 'wb') as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                return filename
        except Exception:
            pass
        return ''

    def getImageFiles(self, host: str, jobId: str, directory: str) -> List[str]:
        """Download all scanned images of a job as files."""
        images = []
        while True:
            filename = self.getImageFile(host, jobId, directory)
            if not filename:
                break
            images.append(filename)
        return images

    def getImageStreams(self, host: str, jobId: str) -> List[bytes]:
        """Get all scanned images as byte streams."""
        streams = []
        url = f"{host}/api/device/scanners/jobs/{jobId}/next-page"
        while True:
            try:
                response = requests.get(url)
                if response.status_code == 200:
                    streams.append(response.content)
                else:
                    break
            except Exception:
                break
        return streams

    def getImageInfo(self, host: str, jobId: str) -> Dict[str, Any]:
        """Get information of the next scanned page."""
        url = f"{host}/api/device/scanners/jobs/{jobId}/next-page-info"
        try:
            response = requests.get(url)
            return response.json()
        except Exception as error:
            return {"error": str(error)}

    def getScannerCapabilities(self, host: str, jobId: str) -> Dict[str, Any]:
        """Get scanner capabilities (e.g., DPI, color mode)."""
        url = f"{host}/api/device/scanners/jobs/{jobId}/scanner/capabilities"
        try:
            response = requests.get(url)
            return response.json()
        except Exception as error:
            return {"error": str(error)}

    ##################
    # Document-related
    ##################

    def createDocument(self, host: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new document."""
        url = f"{host}/api/storage/documents"
        try:
            headers = {
                'Content-Type': 'application/json',
                'Content-Length': str(len(json.dumps(parameters)))
            }
            response = requests.post(url, headers=headers, json=parameters)
            return response.json()
        except Exception as error:
            return {"error": str(error)}

    def getDocumentInfo(self, host: str, docId: str) -> Dict[str, Any]:
        """Get document metadata."""
        url = f"{host}/api/storage/documents/{docId}"
        try:
            response = requests.get(url)
            return response.json()
        except Exception as error:
            return {"error": str(error)}

    def deleteDocument(self, host: str, docId: str) -> bool:
        """Delete an existing document."""
        url = f"{host}/api/storage/documents/{docId}"
        try:
            response = requests.delete(url)
            return response.status_code == 204
        except Exception:
            return False

    def getDocumentFile(self, host: str, docId: str, directory: str) -> str:
        """Download a document (PDF) and save it to disk."""
        url = f"{host}/api/storage/documents/{docId}/content"
        try:
            response = requests.get(url, stream=True)
            if response.status_code == 200:
                filename = f"document_{int(time.time() * 1000)}.pdf"
                path_to_file = os.path.join(directory, filename)
                with open(path_to_file, 'wb') as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                return filename
        except Exception:
            pass
        return ''

    def getDocumentStream(self, host: str, docId: str) -> Optional[bytes]:
        """Get document content as byte stream."""
        url = f"{host}/api/storage/documents/{docId}/content"
        try:
            response = requests.get(url)
            if response.status_code == 200:
                return response.content
        except Exception:
            pass
        return None

    def insertPage(self, host: str, docId: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new page into an existing document."""
        url = f"{host}/api/storage/documents/{docId}/pages"
        try:
            headers = {
                'Content-Type': 'application/json',
                'DWT-DOC-PASSWORD': parameters.get("password", ""),
                'Content-Length': str(len(json.dumps(parameters)))
            }
            response = requests.post(url, headers=headers, json=parameters)
            return response.json()
        except Exception as error:
            return {"error": str(error)}

    def deletePage(self, host: str, docId: str, pageId: str) -> bool:
        """Delete a page from a document."""
        url = f"{host}/api/storage/documents/{docId}/pages/{pageId}"

        try:
            response = requests.delete(url)
            return response.status_code == 204
        except Exception:
            return False
