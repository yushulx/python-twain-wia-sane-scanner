
import sys
import flet as ft
import base64
sys.path.append("../")
from dynamsoftservice import ScannerController, ScannerType

license_key = "DLS2eyJoYW5kc2hha2VDb2RlIjoiMjAwMDAxLTE2NDk4Mjk3OTI2MzUiLCJvcmdhbml6YXRpb25JRCI6IjIwMDAwMSIsInNlc3Npb25QYXNzd29yZCI6IndTcGR6Vm05WDJrcEQ5YUoifQ=="
scannerController = ScannerController()
devices = []
host = "http://127.0.0.1:18622"


def main(page: ft.Page):
    page.title = "Document Scanner"

    def dropdown_changed(e):
        print(dd.value)
        page.update()

    def get_device(e):
        devices.clear()
        dd.options = []
        scanners = scannerController.getDevices(
            host, ScannerType.TWAINSCANNER | ScannerType.TWAINX64SCANNER)
        for i, scanner in enumerate(scanners):
            devices.append(scanner)
            dd.options.append(ft.dropdown.Option(scanner['name']))
            dd.value = scanner['name']

        page.update()

    def scan_document(e):
        if len(devices) == 0:
            return

        device = devices[0]["device"]

        for scanner in devices:
            if scanner['name'] == dd.value:
                device = scanner['device']
                break

        parameters = {
            "license": license_key,
            "device": device,
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
            images = scannerController.getImageStreams(host, job_id)
            for i, image in enumerate(images):
                base64_encoded = base64.b64encode(image)
                display = ft.Image(src_base64=base64_encoded.decode('utf-8'), width=600,
                                   height=600,
                                   fit=ft.ImageFit.CONTAIN,)
                lv.controls.append(display)

            scannerController.deleteJob(host, job_id)

        page.update()

    buttonDevice = ft.ElevatedButton(
        text="Get Devices", on_click=get_device)

    dd = ft.Dropdown(on_change=dropdown_changed,)
    buttonScan = ft.ElevatedButton(text="Scan", on_click=scan_document)
    row = ft.Row(spacing=10, controls=[
                 buttonDevice, dd, buttonScan], alignment=ft.MainAxisAlignment.CENTER,)

    lv = ft.ListView(expand=1, spacing=10, padding=20, auto_scroll=True)

    page.add(
        row, lv
    )


ft.app(main)
# ft.app(target=main, view=ft.AppView.WEB_BROWSER)
