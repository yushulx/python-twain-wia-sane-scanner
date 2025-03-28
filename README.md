# 🐍 Python Document Scanner for TWAIN, WIA, SANE, ICA, and eSCL
This package provides Python bindings to access the **Dynamic Web TWAIN Service REST API**, enabling document scanning across platforms using:

- **TWAIN (32-bit / 64-bit)**
- **WIA (Windows Image Acquisition)**
- **SANE (Linux)**
- **ICA (macOS)**
- **eSCL (AirScan / Mopria)**

## ⚙️ Prerequisites

### ✅ Install Dynamic Web TWAIN Service

- **Windows**: [Dynamsoft-Service-Setup.msi](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup.msi)  
- **macOS**: [Dynamsoft-Service-Setup.pkg](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup.pkg)  
- **Linux**:  
  - [Dynamsoft-Service-Setup.deb](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup.deb)  
  - [Dynamsoft-Service-Setup-arm64.deb](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup-arm64.deb)  
  - [Dynamsoft-Service-Setup-mips64el.deb](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup-mips64el.deb)  
  - [Dynamsoft-Service-Setup.rpm](https://demo.dynamsoft.com/DWT/DWTResources/dist/DynamsoftServiceSetup.rpm)

### 🔑 Get a License

Request a [free trial license](https://www.dynamsoft.com/customer/license/trialLicense/?product=dcv&package=cross-platform).

## 🧩 Configuration

After installation, open `http://127.0.0.1:18625/` in your browser to configure the **host** and **port** settings.

> By default, the service is bound to `127.0.0.1`. To access it across the LAN, change the host to your local IP (e.g., `192.168.8.72`).

![dynamsoft-service-config](https://github.com/yushulx/dynamsoft-service-REST-API/assets/2202306/e2b1292e-dfbd-4821-bf41-70e2847dd51e)


## 📡 REST API Endpoints

[https://www.dynamsoft.com/web-twain/docs/info/api/restful.html](https://www.dynamsoft.com/web-twain/docs/info/api/restful.html)

## 🚀 Quick Start
Replace `LICENSE-KEY` with your actual license and run:

```python
from dynamsoftservice import ScannerController, ScannerType

license_key = "LICENSE-KEY"
scannerController = ScannerController()
devices = []
host = "http://127.0.0.1:18622"

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

            job = scannerController.createJob(host, parameters)
            job_id = job["jobuid"]
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

## 🧪 Examples
- 📦 [Flet App](https://github.com/yushulx/twain-wia-sane-scanner/tree/main/example)

    ![python-flet-twain-document-scanner](https://github.com/yushulx/twain-wia-sane-scanner/assets/2202306/219d2adc-b03c-4da7-8393-10f49cdbc54d)

## 📚 Python API Reference

### Scanner Functions

| Method | Description |
|--------|-------------|
| `getDevices(host, scannerType=None)` | Get available scanning devices |
| `createJob(host, parameters)` | Create a scanning job |
| `checkJob(host, jobId)` | Check job status |
| `updateJob(host, jobId, parameters)` | Update job status |
| `deleteJob(host, jobId)` | Delete a scanning job |
| `getImageFile(host, jobId, directory)` | Get a single scanned image |
| `getImageFiles(host, jobId, directory)` | Get multiple scanned images |
| `getImageStreams(host, jobId)` | Get scanned images as byte streams |
| `getImageInfo(host, jobId)` | Get metadata about next scanned page |
| `getScannerCapabilities(host, jobId)` | Get scanner settings and capabilities |

### Document Functions

| Method | Description |
|--------|-------------|
| `createDocument(host, parameters)` | Create a new document |
| `getDocumentInfo(host, docId)` | Retrieve document metadata |
| `deleteDocument(host, docId)` | Delete an existing document |
| `getDocumentFile(host, docId, directory)` | Download document as PDF file |
| `getDocumentStream(host, docId)` | Get document as byte stream |
| `insertPage(host, docId, parameters)` | Insert a page into a document |
| `deletePage(host, docId, pageId)` | Delete a specific page from a document |


## 📦 Build the Package
To build and distribute the package locally:
- Source distribution:
    
    ```bash
    python setup.py sdist
    ```

- Wheel Distribution:
    
    ```bash
    pip wheel . --verbose
    # Or
    python setup.py bdist_wheel
    ```


