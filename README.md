# Python Document Scanner for TWAIN, WIA, SANE, ICA, and eSCL
The package provides methods for calling [Dynamsoft Service REST APIs](https://www.dynamsoft.com/blog/announcement/dynamsoft-service-restful-api/). This allows developers to build Python applications for digitizing documents from **TWAIN (32-bit/64-bit)**, **WIA**, **SANE**, **ICA** and **eSCL** scanners.

## Prerequisites
1. Install Dynamsoft Service.
    - Windows: [Dynamsoft-Service-Setup.msi](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup.msi)
    - macOS: [Dynamsoft-Service-Setup.pkg](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup.pkg)
    - Linux: 
        - [Dynamsoft-Service-Setup.deb](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup.deb)
        - [Dynamsoft-Service-Setup-arm64.deb](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup-arm64.deb)
        - [Dynamsoft-Service-Setup-mips64el.deb](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup-mips64el.deb)
        - [Dynamsoft-Service-Setup.rpm](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup.rpm)
        
2. Request a [free trial license](https://www.dynamsoft.com/customer/license/trialLicense/?product=dcv&package=cross-platform).

## Dynamsoft Service REST API
By default, the REST API's host address is set to `http://127.0.0.1:18622`. 

| Method | Endpoint        | Description                   | Parameters                         | Response                      |
|--------|-----------------|-------------------------------|------------------------------------|-------------------------------|
| GET    | `/DWTAPI/Scanners`    | Get a list of scanners  | None                               | `200 OK` with scanner list       |
| POST   | `/DWTAPI/ScanJobs`    | Creates a scan job      | `license`, `device`, `config`      | `201 Created` with job ID    |
| GET    | `/DWTAPI/ScanJobs/:id/NextDocument`| Retrieves a document image     | `id`: Job ID   | `200 OK` with image stream    |
| DELETE | `/DWTAPI/ScanJobs/:id`| Deletes a scan job       | `id`: Job ID                      | `200 OK`              |

You can navigate to `http://127.0.0.1:18625/` to access the service. To make it accessible from desktop, mobile, and web applications on the same network, you can change the host address to a LAN IP address. For example, you might use `http://192.168.8.72`.

![dynamsoft-service-config](https://github.com/yushulx/dynamsoft-service-REST-API/assets/2202306/e2b1292e-dfbd-4821-bf41-70e2847dd51e)

The scanner parameter configuration is based on [Dynamsoft Web TWAIN documentation](https://www.dynamsoft.com/web-twain/docs/info/api/Interfaces.html#DeviceConfiguration). 

## Quick Start
Replace the license key in the code below with a valid one and run the code.

```python
from dynamsoftservice import ScannerController, ScannerType

scannerController = ScannerController()
devices = []
host = "http://127.0.0.1:18622"
license_key = "LICENSE-KEY"

questions = """
Please select an operation:
1. Get scanners
2. Acquire documents by scanner index
3. Quit
"""


def ask_question():
    while True:
        print(".............................................")
        answer = input(questions)

        if answer == '3':
            break
        elif answer == '1':
            scanners = scannerController.getDevices(
                host, ScannerType.TWAINSCANNER | ScannerType.TWAINX64SCANNER)
            devices.clear()
            for i, scanner in enumerate(scanners):
                devices.append(scanner)
                print(f"\nIndex: {i}, Name: {scanner['name']}")
        elif answer == '2':
            if len(devices) == 0:
                print("Please get scanners first!\n")
                continue

            index = input(f"\nSelect an index (<= {len(devices) - 1}): ")
            index = int(index)  

            if index < 0 or index >= len(devices):
                print("It is out of range.")
                continue

            parameters = {
                "license": license_key,
                "device": devices[index]["device"],
            }

            parameters["config"] = {
                "IfShowUI": False,
                "PixelType": 2,
                "Resolution": 200,
                "IfFeederEnabled": False,
                "IfDuplexEnabled": False,
            }

            job_id = scannerController.scanDocument(host, parameters)

            if job_id != "":
                images = scannerController.getImageFiles(host, job_id, "./")
                for i, image in enumerate(images):
                    print(f"Image {i}: {image}")

                scannerController.deleteJob(host, job_id)
        else:
            continue


if __name__ == "__main__":
    ask_question()
```

## Example
- [Flet App](https://github.com/yushulx/twain-wia-sane-scanner/tree/main/example)

    ![python-flet-twain-document-scanner](https://github.com/yushulx/twain-wia-sane-scanner/assets/2202306/219d2adc-b03c-4da7-8393-10f49cdbc54d)

## DynamsoftService API
The `DynamsoftService` class provides methods to interact with the Dynamsoft service.

- `getDevices(self, host: str, scannerType: int = None) -> List[Any]`: Get a list of available devices.
- `scanDocument(self, host: str, parameters: Dict[str, Any]) -> str`: Scan a document.
- `deleteJob(self, host: str, jobId: str) -> None`: Delete a job.
- `getImageFile(self, host, job_id, directory)`: Get an image file.
- `getImageFiles(self, host: str, jobId: str, directory: str) -> List[str]`: Get a list of image files.
- `getImageStreams(self, host: str, jobId: str) -> List[bytes]`: Get a list of image streams.

## How to Build the Package
- Source distribution:
    
    ```bash
    python setup.py sdist
    ```

- Wheel:
    
    ```bash
    pip wheel . --verbose
    # Or
    python setup.py bdist_wheel
    ```


