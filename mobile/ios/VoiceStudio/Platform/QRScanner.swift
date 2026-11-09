import SwiftUI
@preconcurrency import AVFoundation
import VoiceStudioKit

struct QRScanner: UIViewControllerRepresentable {
    let onCode: @MainActor (String) -> Void
    let onError: @MainActor (ClientError) -> Void
    func makeUIViewController(context: Context) -> ScannerController { ScannerController(onCode: onCode, onError: onError) }
    func updateUIViewController(_ uiViewController: ScannerController, context: Context) {}
    static func dismantleUIViewController(_ uiViewController: ScannerController, coordinator: ()) { uiViewController.stop() }
}
private final class CaptureWorker: @unchecked Sendable {
    let session = AVCaptureSession()
    let queue = DispatchQueue(label: "com.voicestudio.mobile.qr")
    func start(output: AVCaptureMetadataOutput, delegate: any AVCaptureMetadataOutputObjectsDelegate, completion: @escaping @Sendable (Bool) -> Void) {
        let box = MetadataDelegate(delegate, output: output)
        queue.async {
            let output = box.output
            do {
                guard let device = AVCaptureDevice.default(for: .video) else { completion(false); return }
                let input = try AVCaptureDeviceInput(device: device)
                self.session.beginConfiguration()
                guard self.session.canAddInput(input), self.session.canAddOutput(output) else { self.session.commitConfiguration(); completion(false); return }
                self.session.addInput(input); self.session.addOutput(output)
                output.setMetadataObjectsDelegate(box.value, queue: .main); output.metadataObjectTypes = [.qr]
                self.session.commitConfiguration(); self.session.startRunning(); completion(true)
            } catch { completion(false) }
        }
    }
    func stop() { queue.async { self.session.stopRunning() } }
    private final class MetadataDelegate: @unchecked Sendable {
        let value: any AVCaptureMetadataOutputObjectsDelegate
        let output: AVCaptureMetadataOutput
        init(_ value: any AVCaptureMetadataOutputObjectsDelegate, output: AVCaptureMetadataOutput) { self.value = value; self.output = output }
    }
}
final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    private let worker = CaptureWorker()
    private var preview: AVCaptureVideoPreviewLayer?
    private var task: Task<Void, Never>?
    private var delivered = false
    private let onCode: @MainActor (String) -> Void
    private let onError: @MainActor (ClientError) -> Void
    init(onCode: @escaping @MainActor (String) -> Void, onError: @escaping @MainActor (ClientError) -> Void) { self.onCode = onCode; self.onError = onError; super.init(nibName: nil, bundle: nil) }
    required init?(coder: NSCoder) { return nil }
    override func viewDidLoad() {
        super.viewDidLoad()
        task = Task {
            guard await AVCaptureDevice.requestAccess(for: .video) else { onError(ClientError("mobile.permissions.denied")); return }
            guard !Task.isCancelled else { return }
            let preview = AVCaptureVideoPreviewLayer(session: worker.session)
            preview.videoGravity = .resizeAspectFill; view.layer.addSublayer(preview); self.preview = preview; preview.frame = view.bounds
            worker.start(output: AVCaptureMetadataOutput(), delegate: self) { [weak self] success in
                if !success { Task { @MainActor in self?.onError(ClientError("mobile.ios.qr_unavailable")) } }
            }
        }
    }
    override func viewDidLayoutSubviews() { super.viewDidLayoutSubviews(); preview?.frame = view.bounds }
    func stop() { task?.cancel(); worker.stop() }
    nonisolated func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject], from connection: AVCaptureConnection) {
        guard let code = (metadataObjects.first as? AVMetadataMachineReadableCodeObject)?.stringValue else { return }
        Task { @MainActor in guard !delivered else { return }; delivered = true; stop(); onCode(code) }
    }
}
