import SwiftUI
import UIKit

@MainActor enum Keyboard {
    static func dismiss() {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }
}

/// Keep SwiftUI's keyboard safe area: scrolling can always reveal the next field/action.
/// Shared by pages and picker sheets with search, including sheets opened from Settings.
private struct KeyboardAccessible: ViewModifier {
    @Environment(AppModel.self) private var model
    func body(content: Content) -> some View {
        content
            .scrollDismissesKeyboard(.interactively)
            .submitLabel(.done)
            .onSubmit(of: [.text, .search]) { Keyboard.dismiss() }
            .background(KeyboardDismissRegion().allowsHitTesting(false))
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button(model.t("mobile.keyboard.done")) { Keyboard.dismiss() }
                        .studioButton(.tertiary)
                        .accessibilityIdentifier("keyboard.done")
                }
            }
    }
}

extension View {
    func keyboardAccessible() -> some View { modifier(KeyboardAccessible()) }
}

/// A non-cancelling gesture observes taps outside native text inputs. A SwiftUI tap
/// gesture on the whole page would also dismiss a field as the user tries to focus it.
private struct KeyboardDismissRegion: UIViewRepresentable {
    func makeUIView(context: Context) -> KeyboardDismissView { KeyboardDismissView() }
    func updateUIView(_ uiView: KeyboardDismissView, context: Context) {}
    static func dismantleUIView(_ uiView: KeyboardDismissView, coordinator: ()) { uiView.detach() }
}

private final class KeyboardDismissView: UIView, UIGestureRecognizerDelegate {
    private lazy var tap = UITapGestureRecognizer(target: self, action: #selector(dismissKeyboard))

    override func didMoveToWindow() {
        super.didMoveToWindow()
        detach()
        tap.cancelsTouchesInView = false
        tap.delegate = self
        window?.addGestureRecognizer(tap)
    }

    func detach() { tap.view?.removeGestureRecognizer(tap) }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
        // Include navigation headers and surrounding chrome, not just page whitespace.
        guard window != nil else { return false }
        var ancestor: UIView? = self
        while let view = ancestor {
            if view.isHidden || view.alpha == 0 { return false }
            ancestor = view.superview
        }
        ancestor = touch.view
        while let view = ancestor {
            if view is UITextField || view is UITextView || view is UISearchBar { return false }
            ancestor = view.superview
        }
        return true
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool { true }

    @objc private func dismissKeyboard() { window?.endEditing(true) }
}
