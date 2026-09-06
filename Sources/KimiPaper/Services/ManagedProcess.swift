import Foundation
import Darwin

@MainActor
final class ManagedProcess {
    private let process = Process()
    private let output = Pipe()
    private let control = Pipe()
    private var pending = Data()
    private var stopped = false
    var onLine: ((String) -> Void)?
    var onExit: (() -> Void)?
    var running: Bool { process.isRunning }

    init(paths: RuntimePaths, executable: URL, arguments: [String], cwd: URL, localOnly: Bool, extraEnvironment: [String: String] = [:]) throws {
        // Supervisor owns a separate child process group and kills it when our stdin closes.
        // This also handles a crashed app without touching unrelated Kimi processes.
        process.executableURL = paths.python
        process.arguments = [paths.supervisor.path, executable.path] + arguments
        process.currentDirectoryURL = cwd
        process.environment = paths.environment(localOnly: localOnly).merging(extraEnvironment) { _, new in new }
        process.standardInput = control
        process.standardOutput = output
        process.standardError = output
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            Task { @MainActor [weak self] in self?.consume(data) }
        }
        process.terminationHandler = { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, !self.stopped else { return }
                self.onExit?()
            }
        }
        try process.run()
    }

    private func consume(_ data: Data) {
        guard !stopped else { return }
        pending.append(data)
        while let newline = pending.firstIndex(of: 10) {
            let line = String(decoding: pending[..<newline], as: UTF8.self)
            pending.removeSubrange(...newline)
            onLine?(line)
        }
        // Startup credentials stay transient in memory; never persist stdout or render it in UI.
        if pending.count > 16384 { pending.removeAll(keepingCapacity: false) }
    }

    func stop() {
        guard !stopped else { return }
        stopped = true
        onLine = nil; onExit = nil
        pending.removeAll(keepingCapacity: false)
        output.fileHandleForReading.readabilityHandler = nil
        try? control.fileHandleForWriting.close()
        // Closing this dedicated pipe asks the supervisor to terminate only its owned group.
    }
}
