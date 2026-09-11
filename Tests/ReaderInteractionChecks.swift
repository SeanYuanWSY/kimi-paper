import AppKit

@main struct ReaderInteractionChecks {
    @MainActor static func main() {
        let interaction = ReaderInteraction()
        let coordinator = NativeDocumentPreview.Coordinator(interaction: interaction)
        let container = NSView(), text = NSTextView()
        text.string = "ALPHA selected passage"
        coordinator.text = text
        coordinator.install(container)
        text.setSelectedRange(NSRange(location: 0, length: 5))
        NotificationCenter.default.post(name: NSTextView.didChangeSelectionNotification, object: text)
        RunLoop.main.run(until: Date().addingTimeInterval(0.15))
        precondition(interaction.quote == "ALPHA", "released native selection must open a card")

        text.setSelectedRange(NSRange(location: 6, length: 8))
        NotificationCenter.default.post(name: NSTextView.didChangeSelectionNotification, object: text)
        interaction.dismiss()
        RunLoop.main.run(until: Date().addingTimeInterval(0.15))
        precondition(interaction.quote.isEmpty, "dismiss must invalidate delayed selection capture")

        NotificationCenter.default.post(name: NSTextView.didChangeSelectionNotification, object: text)
        coordinator.stop()
        RunLoop.main.run(until: Date().addingTimeInterval(0.15))
        precondition(interaction.quote.isEmpty, "dismantled reader must not reopen its selection")
        print("Native reader selection/dismissal checks passed")
    }
}
