from dynamsoftservice import JobStatus, ScannerController, ScannerType

license_key = "DLS2eyJoYW5kc2hha2VDb2RlIjoiMjAwMDAxLTE2NDk4Mjk3OTI2MzUiLCJvcmdhbml6YXRpb25JRCI6IjIwMDAwMSIsInNlc3Npb25QYXNzd29yZCI6IndTcGR6Vm05WDJrcEQ5YUoifQ=="
scannerController = ScannerController()
devices = []
host = "http://127.0.0.1:18622"

info = scannerController.getServerInfo(host)
print(f"Server version: {info}")

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
            index = int(index)  # assuming input is always a number

            if index < 0 or index >= len(devices):
                print("It is out of range.")
                continue

            parameters = {
                "license": license_key,
                "device": devices[index]["device"],
                "autoRun": True,
            }

            parameters["config"] = {
                "IfShowUI": False,
                "PixelType": 2,
                "Resolution": 200,
                "IfFeederEnabled": True,
                "IfDuplexEnabled": False,
            }

            job = scannerController.createJob(host, parameters)
            job_id = job["jobuid"]
            if job_id != "":

                # info = scannerController.checkJob(host, job_id)
                # print(f"Job status: {info}")

                # info = scannerController.getScannerCapabilities(host, job_id)
                # print(f"Scanner capabilities: {info}")

                # info = scannerController.updateJob(
                #     host, job_id, {"status": JobStatus.RUNNING})

                doc = scannerController.createDocument(host, {})
                # print(f"Document: {doc}")

                images = scannerController.getImageFiles(host, job_id, "./")
                for i, image in enumerate(images):
                    print(f"Image {i}: {image}")

                    info = scannerController.getImageInfo(host, job_id)
                    # # print(f"Image info: {info}")

                    info = scannerController.insertPage(host, doc['uid'], { 'password': '', 'source': info['url'] })
                    # # print(f"Insert page: {info}")

                    # info = scannerController.deletePage(host, doc['uid'], info['pages'][0]['uid'])
                    # print(f"Delete page: {info}")

                info = scannerController.getDocumentInfo(host, doc['uid'])
                print(f"Document info: {info}")

                info = scannerController.getDocumentFile(host, doc['uid'], "./")    
                print(f"Document file: {info}")

                info = scannerController.deleteDocument(host, doc['uid'])
                print(f"Delete document: {info}")

                scannerController.deleteJob(host, job_id)
        else:
            continue


if __name__ == "__main__":
    ask_question()
