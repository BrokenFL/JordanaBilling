import AppKit
import Foundation

func showAlert(title: String, message: String, buttons: [String] = ["OK"]) -> NSApplication.ModalResponse {
    let app = NSApplication.shared
    app.setActivationPolicy(.regular)
    app.activate(ignoringOtherApps: true)
    let alert = NSAlert()
    alert.messageText = title
    alert.informativeText = message
    alert.alertStyle = .critical
    for button in buttons {
        alert.addButton(withTitle: button)
    }
    return alert.runModal()
}

let bundle = Bundle.main
guard let scriptPath = bundle.path(forResource: "launch_installed_app", ofType: "sh") else {
    _ = showAlert(
        title: "Jordana Billing Cannot Launch",
        message: "The launch helper is missing. Run Install Jordana Billing again."
    )
    exit(1)
}

let resourceURL = bundle.resourceURL ?? URL(fileURLWithPath: scriptPath).deletingLastPathComponent()
let runtimePython = resourceURL.appendingPathComponent("runtime/venv/bin/python")
let bundleURL = bundle.bundleURL
let releaseRoot = bundleURL.deletingLastPathComponent().deletingLastPathComponent()
let setupApp = releaseRoot.appendingPathComponent("Install Jordana Billing.app")

if !FileManager.default.isExecutableFile(atPath: runtimePython.path) && FileManager.default.fileExists(atPath: setupApp.path) {
    let response = showAlert(
        title: "Jordana Billing Has Not Been Installed Yet",
        message: "Open Install Jordana Billing instead. The app inside the release payload is not the installed daily app.",
        buttons: ["Open Installer", "Cancel"]
    )
    if response == .alertFirstButtonReturn {
        NSWorkspace.shared.open(setupApp)
    }
    exit(1)
}

let process = Process()
process.executableURL = URL(fileURLWithPath: "/bin/bash")
process.arguments = [scriptPath]
process.currentDirectoryURL = resourceURL

do {
    try process.run()
    process.waitUntilExit()
    exit(process.terminationStatus)
} catch {
    _ = showAlert(
        title: "Jordana Billing Cannot Launch",
        message: "The launch helper could not start. Run Install Jordana Billing again."
    )
    exit(1)
}
