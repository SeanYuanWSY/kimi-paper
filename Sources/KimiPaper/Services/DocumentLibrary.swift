import Foundation
import CryptoKit
import Darwin

enum ReaderKind: String, Sendable {
    case latex, pdf, markdown, text, image, unsupported
    static func of(_ url: URL) -> ReaderKind {
        switch url.pathExtension.lowercased() {
        case "tex": return .latex
        case "pdf": return .pdf
        case "md", "markdown": return .markdown
        case "txt", "bib", "csv", "tsv", "json", "yaml", "yml", "log", "py", "r", "swift", "js", "ts", "css", "toml": return .text
        case "png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "heic": return .image
        default: return .unsupported
        }
    }
    var limit: Int { self == .pdf ? 64 * 1024 * 1024 : (self == .image ? 20 * 1024 * 1024 : 4 * 1024 * 1024) }
}

struct ReaderEntry: Identifiable, Sendable {
    let url: URL
    let directory: Bool
    var id: String { url.path }
    var kind: ReaderKind { ReaderKind.of(url) }
}

struct ReaderSnapshot: Sendable {
    let url: URL
    let kind: ReaderKind
    let bytes: Data
    let digest: String
}

// Open each component relative to a directory descriptor. A replaced parent or
// symlink cannot redirect the preview outside the explicitly selected folder.
enum DocumentLibrary {
    static let skipped = Set(["node_modules", "__pycache__", "venv", "env", "build", "dist", "credentials", "oauth", "secrets"])
    static let packages = Set(["app", "bundle", "framework", "photoslibrary"])

    private static func excluded(_ name: String) -> Bool {
        name.hasPrefix(".") || skipped.contains(name.lowercased()) || packages.contains(URL(fileURLWithPath: name).pathExtension.lowercased())
            || name.lowercased().range(of: #"(^|[-_.])(auth|credentials?|secrets?|tokens?|passwords?|oauth|apikey|api_key)([-_.]|$)"#, options: .regularExpression) != nil
    }

    static func validateRoot(_ root: URL) throws {
        let home = FileManager.default.homeDirectoryForCurrentUser.standardizedFileURL
        let value = root.standardizedFileURL
        let privatePaths = [".kimi-code", ".codex", ".ssh", ".agents", ".aws", ".gnupg", "Library/Keychains", "Library/Application Support/Kimi Paper"]
        let forbidden = privatePaths.map { home.appendingPathComponent($0).path }
        guard value.path != home.path, !home.path.hasPrefix(value.path + "/"), value.path != "/",
              !forbidden.contains(where: { value.path == $0 || value.path.hasPrefix($0 + "/") || $0.hasPrefix(value.path + "/") }),
              !["/System", "/Library", "/Applications", "/usr", "/bin", "/sbin", "/etc", "/private", "/var", "/tmp", "/opt"].contains(where: { value.path == $0 || value.path.hasPrefix($0 + "/") }),
              value.resolvingSymlinksInPath().path == value.path else {
            throw AppFailure.message("请选择具体项目文件夹，不要选择用户主目录、系统目录或应用私有目录。")
        }
        let fd = try descriptor(value, root: value, directory: true)
        close(fd)
    }

    private static func descriptor(_ file: URL, root: URL, directory: Bool) throws -> Int32 {
        let base = root.standardizedFileURL.path, path = file.standardizedFileURL.path
        guard path == base || path.hasPrefix(base + "/"), root.resolvingSymlinksInPath().path == base else {
            throw AppFailure.message("文件已移出所选文件夹，请重新选择。")
        }
        var fd = open("/", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else { throw AppFailure.message("文件夹无法读取。") }
        for part in base.split(separator: "/") {
            let next = openat(fd, String(part), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            close(fd)
            guard next >= 0 else { throw AppFailure.message("文件夹已移动或包含符号链接，请重新选择。") }
            fd = next
        }
        let parts = path == base ? [] : String(path.dropFirst(base.count + 1)).split(separator: "/").map(String.init)
        for (index, part) in parts.enumerated() {
            guard !excluded(part) else {
                close(fd); throw AppFailure.message("隐藏文件、缓存及应用包不在文档浏览范围内。")
            }
            let flags = O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK | ((directory || index < parts.count - 1) ? O_DIRECTORY : 0)
            let next = openat(fd, part, flags)
            close(fd)
            guard next >= 0 else { throw AppFailure.message("文件已移动、不可读取或是符号链接，请刷新列表。") }
            fd = next
        }
        var info = stat()
        guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == (directory ? S_IFDIR : S_IFREG) else {
            close(fd); throw AppFailure.message("只能预览普通文件。")
        }
        return fd
    }

    static func entries(in folder: URL, root: URL) throws -> [ReaderEntry] {
        let fd = try descriptor(folder, root: root, directory: true)
        guard let stream = fdopendir(fd) else { close(fd); throw AppFailure.message("无法列出文件夹。") }
        defer { closedir(stream) }
        var result: [ReaderEntry] = []
        while let entry = readdir(stream) {
            let name = withUnsafePointer(to: &entry.pointee.d_name) {
                $0.withMemoryRebound(to: CChar.self, capacity: Int(NAME_MAX) + 1) { String(cString: $0) }
            }
            guard !excluded(name) else { continue }
            var info = stat()
            guard fstatat(fd, name, &info, AT_SYMLINK_NOFOLLOW) == 0 else { continue }
            let mode = info.st_mode & S_IFMT
            guard mode == S_IFDIR || mode == S_IFREG else { continue }
            result.append(ReaderEntry(url: folder.appendingPathComponent(name), directory: mode == S_IFDIR))
            guard result.count <= 2000 else { throw AppFailure.message("这个目录超过 2000 个文件，请选择更具体的文件夹。") }
        }
        return result.sorted { a, b in
            a.directory != b.directory ? a.directory : a.url.lastPathComponent.localizedStandardCompare(b.url.lastPathComponent) == .orderedAscending
        }
    }

    static func read(_ file: URL, root: URL) throws -> ReaderSnapshot {
        let kind = ReaderKind.of(file)
        guard kind != .unsupported else { throw AppFailure.message("这种格式暂不支持内置预览。可以在 Finder 中打开。") }
        let fd = try descriptor(file, root: root, directory: false)
        defer { close(fd) }
        var before = stat()
        guard fstat(fd, &before) == 0, before.st_size <= kind.limit else { throw AppFailure.message("文件较大，请在外部应用中打开（文本 4 MB、图片 20 MB、PDF 64 MB）。") }
        var data = Data(), buffer = [UInt8](repeating: 0, count: 65536)
        while true {
            try Task.checkCancellation()
            let count = Darwin.read(fd, &buffer, min(buffer.count, kind.limit + 1 - data.count))
            guard count >= 0 else { throw AppFailure.message("读取文件失败。") }
            if count == 0 { break }
            data.append(contentsOf: buffer.prefix(count))
            guard data.count <= kind.limit else { throw AppFailure.message("文件超过预览大小限制。") }
        }
        var after = stat()
        guard fstat(fd, &after) == 0, before.st_size == after.st_size,
              before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec,
              before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec else {
            throw AppFailure.message("文件正在写入，请稍后刷新。")
        }
        return ReaderSnapshot(url: file, kind: kind, bytes: data,
                              digest: SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined())
    }

    static func validateLatexEntry(_ file: URL, root: URL) throws {
        // Missing entries remain supported for starting a new paper; an existing
        // entry (including a broken symlink) must pass the same safe reader.
        let parent = file.deletingLastPathComponent()
        let parentFD = try descriptor(parent, root: root, directory: true)
        defer { close(parentFD) }
        var info = stat()
        if fstatat(parentFD, file.lastPathComponent, &info, AT_SYMLINK_NOFOLLOW) == 0 {
            _ = try read(file, root: root)
        } else if errno != ENOENT { throw AppFailure.message("LaTeX 主文件无法读取。") }
    }
}
