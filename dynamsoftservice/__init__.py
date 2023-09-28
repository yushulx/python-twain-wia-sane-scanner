import time
import os
import json
import requests
from typing import List, Any, Dict

__version__ = '1.0.0'


class ScannerType:
    """
    A class that defines constants for different types of scanners.

    Attributes:
        TWAINSCANNER (int): TWAIN scanner type.
        WIASCANNER (int): WIA scanner type.
        TWAINX64SCANNER (int): TWAIN 64-bit scanner type.
        ICASCANNER (int): ICA scanner type.
        SANESCANNER (int): SANE scanner type.
        ESCLSCANNER (int): ESCL scanner type.
        WIFIDIRECTSCANNER (int): WiFi Direct scanner type.
        WIATWAINSCANNER (int): WIA TWAIN scanner type.
    """
    TWAINSCANNER = 0x10
    WIASCANNER = 0x20
    TWAINX64SCANNER = 0x40
    ICASCANNER = 0x80
    SANESCANNER = 0x100
    ESCLSCANNER = 0x200
    WIFIDIRECTSCANNER = 0x400
    WIATWAINSCANNER = 0x800


class ScannerController:
    """
    A class that provides methods to interact with Dynamsoft Service API.
    """

    def getDevices(self, host: str, scannerType: int = None) -> List[Any]:
        """
        Get a list of available devices.

        Args:
            host (str): The URL of the Dynamsoft Service API.
            scannerType (int, optional): The type of scanner. Defaults to None.

        Returns:
            List[Any]: A list of available devices.
        """
        devices = []
        url = f"{host}/DWTAPI/Scanners"
        if scannerType is not None:
            url += f"?type={scannerType}"

        try:
            response = requests.get(url)
            if response.status_code == 200 and response.text:
                devices = json.loads(response.text)
                return devices
        except Exception as error:
            pass
        return []

    def scanDocument(self, host: str, parameters: Dict[str, Any]) -> str:
        """
        Scan a document.

        Args:
            host (str): The URL of the Dynamsoft Service API.
            parameters (Dict[str, Any]): The parameters for the scan.

        Returns:
            str: The ID of the job.
        """
        url = f"{host}/DWTAPI/ScanJobs"
        try:
            response = requests.post(url, json=parameters, headers={
                                     'Content-Type': 'application/text'})
            jobId = response.text
            if response.status_code == 201:
                return jobId
        except Exception as error:
            pass
        return ""

    def deleteJob(self, host: str, jobId: str) -> None:
        """
        Delete a job.

        Args:
            host (str): The URL of the Dynamsoft Service API.
            jobId (str): The ID of the job.
        """
        if not jobId:
            return
        url = f"{host}/DWTAPI/ScanJobs/{jobId}"
        try:
            response = requests.delete(url)
            if response.status_code == 200:
                pass
        except Exception as error:
            pass

    def get_image_file(self, host, job_id, directory):
        """
        Get an image file.

        Args:
            host (str): The URL of the Dynamsoft Service API.
            jobId (str): The ID of the job.
            directory (str): The directory to save the image file.

        Returns:
            str: The image file path.
        """
        url = f"{host}/DWTAPI/ScanJobs/{job_id}/NextDocument"
        try:
            response = requests.get(url, stream=True)
            if response.status_code == 200:
                timestamp = str(int(time.time() * 1000))
                filename = f"image_{timestamp}.jpg"
                image_path = os.path.join(directory, filename)
                with open(image_path, 'wb') as f:
                    f.write(response.content)
                return filename
        except Exception as e:
            print("No more images.")
            return ''
        return ''

    def getImageFiles(self, host: str, jobId: str, directory: str) -> List[str]:
        """
        Get a list of image files.

        Args:
            host (str): The URL of the Dynamsoft Service API.
            jobId (str): The ID of the job.
            directory (str): The directory to save the image files.

        Returns:
            List[str]: A list of image file paths.
        """
        images = []
        while True:
            filename = self.get_image_file(host, jobId, directory)
            if filename == '':
                break
            else:
                images.append(filename)
        return images

    def getImageStreams(self, host: str, jobId: str) -> List[bytes]:
        """
        Get a list of image streams.

        Args:
            host (str): The URL of the Dynamsoft Service API.
            jobId (str): The ID of the job.

        Returns:
            List[bytes]: A list of image streams.
        """
        streams = []
        url = f"{host}/DWTAPI/ScanJobs/{jobId}/NextDocument"
        while True:
            try:
                response = requests.get(url)
                if response.status_code == 200:
                    streams.append(response.content)
                elif response.status_code == 410:
                    break
            except Exception as error:
                break
        return streams
