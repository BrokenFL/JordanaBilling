import AppKit
import Foundation

final class SetupController: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let window = NSWindow(
        contentRect: NSRect(x: 0, y: 0, width: 560, height: 470),
        styleMask: [.titled, .closable, .miniaturizable],
        backing: .buffered,
        defer: false
    )
    private let status = NSTextField(labelWithString: "")
    private let urlField = NSTextField()
    private let keyField = NSSecureTextField()
    private let cleanStart = NSButton(checkboxWithTitle: "Initialize a clean production database if one is not already present.", target: nil, action: nil)
    private let installButton = NSButton(title: "Install", target: nil, action: nil)
    private let openButton = NSButton(title: "Open Jordana Billing", target: nil, action: nil)
    private let progress = NSProgressIndicator()

    private let fm = FileManager.default
    private var payloadRoot: URL {
        let resources = Bundle.main.resourceURL ?? Bundle.main.bundleURL.appendingPathComponent("Contents/Resources")
        return resources.appendingPathComponent("ReleasePayload")
    }
    private var supportRoot: URL {
        URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support/Jordana Billing")
    }
    private var configPath: URL {
        supportRoot.appendingPathComponent("config/.env")
    }
    private var dbPath: URL {
        supportRoot.appendingPathComponent("data/jordana_invoice.sqlite3")
    }
    private var installedApp: URL {
        URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Applications/Jordana Billing.app")
    }
    private var requiredPython: String?
    private var selectedPython: URL?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        buildUI()
        detectEnvironment()
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)
        return true
    }

    private func buildMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)

        let appMenu = NSMenu()
        let quitTitle = "Quit Install Jordana Billing"
        let quitItem = NSMenuItem(title: quitTitle, action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appMenu.addItem(quitItem)
        appMenuItem.submenu = appMenu
        NSApp.mainMenu = mainMenu
    }

    private func buildUI() {
        window.title = "Install Jordana Billing"
        window.delegate = self
        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .leading
        root.spacing = 12
        root.edgeInsets = NSEdgeInsets(top: 24, left: 24, bottom: 20, right: 24)
        root.translatesAutoresizingMaskIntoConstraints = false
        window.contentView = root

        let title = NSTextField(labelWithString: "Jordana Billing Installer")
        title.font = .boldSystemFont(ofSize: 22)
        root.addArrangedSubview(title)

        let intro = NSTextField(wrappingLabelWithString: "This installs the private local billing app to ~/Applications and keeps configuration and the SQLite database in Application Support. Existing private config and data are preserved.")
        intro.preferredMaxLayoutWidth = 500
        root.addArrangedSubview(intro)

        root.addArrangedSubview(status)

        let urlLabel = NSTextField(labelWithString: "Apps Script URL")
        root.addArrangedSubview(urlLabel)
        urlField.placeholderString = "https://script.google.com/..."
        urlField.translatesAutoresizingMaskIntoConstraints = false
        root.addArrangedSubview(urlField)
        urlField.widthAnchor.constraint(equalToConstant: 500).isActive = true

        let keyLabel = NSTextField(labelWithString: "Ingest API key")
        root.addArrangedSubview(keyLabel)
        keyField.placeholderString = "Input hidden"
        keyField.translatesAutoresizingMaskIntoConstraints = false
        root.addArrangedSubview(keyField)
        keyField.widthAnchor.constraint(equalToConstant: 500).isActive = true

        cleanStart.state = .off
        cleanStart.lineBreakMode = .byWordWrapping
        root.addArrangedSubview(cleanStart)

        let cleanCopy = NSTextField(wrappingLabelWithString: "Clean-start means unresolved review evidence will sync from Google Sheets, but old invoices, payments, clients, approved sessions, and billing relationships will not be imported.")
        cleanCopy.preferredMaxLayoutWidth = 500
        root.addArrangedSubview(cleanCopy)

        progress.style = .spinning
        progress.isDisplayedWhenStopped = false
        root.addArrangedSubview(progress)

        let buttons = NSStackView()
        buttons.orientation = .horizontal
        buttons.spacing = 10
        buttons.alignment = .centerY
        installButton.target = self
        installButton.action = #selector(install)
        openButton.target = self
        openButton.action = #selector(openInstalledApp)
        openButton.isEnabled = false
        buttons.addArrangedSubview(installButton)
        buttons.addArrangedSubview(openButton)
        root.addArrangedSubview(buttons)
    }

    private func detectEnvironment() {
        var messages: [String] = []
        #if arch(arm64)
        messages.append("Apple Silicon: native arm64 installer.")
        #else
        messages.append("This installer is not running as arm64. Use the Apple Silicon release.")
        installButton.isEnabled = false
        #endif
        let manifest = payloadRoot.appendingPathComponent("release_manifest.json")
        if let data = try? Data(contentsOf: manifest),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let runtime = json["runtime"] as? [String: Any],
           let required = runtime["requires_python"] as? String {
            requiredPython = required
            messages.append("Required Python: \(required).")
            if let python = findCompatiblePython(required: required) {
                selectedPython = python
                messages.append("Using Python: \(python.path)")
            } else {
                messages.append("Compatible Python \(required) was not found. Install Python \(required) from python.org or Homebrew, then reopen this installer.")
                installButton.isEnabled = false
            }
        } else {
            messages.append("Release manifest missing or unreadable.")
            installButton.isEnabled = false
        }
        if fm.fileExists(atPath: configPath.path) {
            messages.append("Existing private config found and will be preserved.")
            urlField.isEnabled = false
            keyField.isEnabled = false
        }
        if fm.fileExists(atPath: dbPath.path) {
            messages.append("Existing database found and will be preserved.")
            cleanStart.isEnabled = false
        }
        status.stringValue = messages.joined(separator: "\n")
    }

    private func validURL(_ value: String) -> Bool {
        guard let url = URL(string: value), url.scheme == "https", url.host != nil else {
            return false
        }
        return !value.contains(" ")
    }

    private func requiredPythonPrefix(_ required: String) -> String? {
        let parts = required.split(separator: ".")
        guard parts.count >= 2 else {
            return nil
        }
        return "\(parts[0]).\(parts[1])."
    }

    private func pythonCandidates(required: String) -> [URL] {
        let majorMinor = required.split(separator: ".").prefix(2).joined(separator: ".")
        let environment = ProcessInfo.processInfo.environment
        var paths: [String] = []
        if let override = environment["JORDANA_INSTALL_PYTHON"], !override.isEmpty {
            paths.append(override)
        }
        paths.append(contentsOf: [
            "/usr/local/bin/python3",
            "/opt/homebrew/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/\(majorMinor)/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
            "/usr/bin/python3",
        ])
        if let pathEnv = environment["PATH"] {
            for directory in pathEnv.split(separator: ":") {
                paths.append("\(directory)/python3")
            }
        }

        var seen = Set<String>()
        return paths.compactMap { raw in
            let expanded = NSString(string: raw).expandingTildeInPath
            guard !expanded.isEmpty && seen.insert(expanded).inserted else {
                return nil
            }
            return URL(fileURLWithPath: expanded)
        }
    }

    private func findCompatiblePython(required: String) -> URL? {
        guard let prefix = requiredPythonPrefix(required) else {
            return nil
        }
        for candidate in pythonCandidates(required: required) {
            guard fm.isExecutableFile(atPath: candidate.path) else {
                continue
            }
            let process = Process()
            process.executableURL = candidate
            process.arguments = ["-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"]
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = Pipe()
            do {
                try process.run()
                process.waitUntilExit()
            } catch {
                continue
            }
            guard process.terminationStatus == 0 else {
                continue
            }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let version = (String(data: data, encoding: .utf8) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if version.hasPrefix(prefix) {
                return candidate
            }
        }
        return nil
    }

    @objc private func install() {
        let hasConfig = fm.fileExists(atPath: configPath.path)
        let hasDb = fm.fileExists(atPath: dbPath.path)
        let appsURL = urlField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        let apiKey = keyField.stringValue

        if !hasConfig {
            guard validURL(appsURL) else {
                showMessage("Enter a valid https Apps Script URL.")
                return
            }
            guard !apiKey.isEmpty else {
                showMessage("Enter the ingest API key.")
                return
            }
        }
        if !hasDb && cleanStart.state != .on {
            showMessage("Confirm clean-start database initialization before installing.")
            return
        }
        if !hasDb {
            let alert = NSAlert()
            alert.messageText = "Initialize a clean production database?"
            alert.informativeText = "This creates an empty local SQLite database. Unresolved review evidence will sync, but historical invoices, payments, clients, relationships, and approved sessions will not be imported."
            alert.addButton(withTitle: "Initialize Clean Database")
            alert.addButton(withTitle: "Cancel")
            if alert.runModal() != .alertFirstButtonReturn {
                return
            }
        }

        installButton.isEnabled = false
        progress.startAnimation(nil)
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                if !hasConfig {
                    try self.writeConfig(appsURL: appsURL, apiKey: apiKey)
                }
                try self.runInstaller(initEmptyDb: !hasDb)
                DispatchQueue.main.async {
                    self.progress.stopAnimation(nil)
                    self.showMessage("Jordana Billing was installed successfully.")
                    self.openButton.isEnabled = true
                    self.installButton.isEnabled = true
                }
            } catch {
                DispatchQueue.main.async {
                    self.progress.stopAnimation(nil)
                    self.installButton.isEnabled = true
                    self.showMessage(error.localizedDescription)
                }
            }
        }
    }

    private func writeConfig(appsURL: String, apiKey: String) throws {
        try fm.createDirectory(at: configPath.deletingLastPathComponent(), withIntermediateDirectories: true)
        let body = "JORDANA_APPS_SCRIPT_URL=\(appsURL)\nJORDANA_INGEST_API_KEY=\(apiKey)\n"
        let tmp = configPath.deletingLastPathComponent().appendingPathComponent(".env.tmp.\(UUID().uuidString)")
        try body.write(to: tmp, atomically: true, encoding: .utf8)
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: tmp.path)
        try fm.moveItem(at: tmp, to: configPath)
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: configPath.path)
    }

    private func runInstaller(initEmptyDb: Bool) throws {
        let script = payloadRoot.appendingPathComponent("scripts/install_release.sh")
        guard fm.isExecutableFile(atPath: script.path) else {
            throw NSError(domain: "JordanaSetup", code: 1, userInfo: [NSLocalizedDescriptionKey: "Installer script is missing from the release payload."])
        }
        guard let python = selectedPython else {
            let required = requiredPython ?? "the required version"
            throw NSError(domain: "JordanaSetup", code: 2, userInfo: [NSLocalizedDescriptionKey: "Compatible Python \(required) was not found."])
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = initEmptyDb ? [script.path, "--init-empty-db", "--yes"] : [script.path]
        process.currentDirectoryURL = payloadRoot
        var environment = ProcessInfo.processInfo.environment
        environment["JORDANA_INSTALL_PYTHON"] = python.path
        process.environment = environment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        try process.run()
        process.waitUntilExit()
        if process.terminationStatus != 0 {
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            let output = String(data: data, encoding: .utf8) ?? "Installation failed."
            throw NSError(domain: "JordanaSetup", code: Int(process.terminationStatus), userInfo: [NSLocalizedDescriptionKey: sanitize(output)])
        }
    }

    private func sanitize(_ text: String) -> String {
        var value = text.replacingOccurrences(of: #"https://[^\s]+"#, with: "[URL]", options: .regularExpression)
        value = value.replacingOccurrences(of: #"jb_[0-9A-Fa-f]{8,}"#, with: "[REDACTED]", options: .regularExpression)
        return value
    }

    private func showMessage(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Install Jordana Billing"
        alert.informativeText = sanitize(message)
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    @objc private func openInstalledApp() {
        NSWorkspace.shared.open(installedApp)
    }
}

let app = NSApplication.shared
let delegate = SetupController()
app.delegate = delegate
app.run()
