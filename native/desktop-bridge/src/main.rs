mod cleanup;
mod shortcuts;
mod watch_folder_core;
#[cfg(target_os = "linux")]
mod wayland_shortcut_core;
mod dictation_output;

use dictation_output::{CaptureOrigin, DictationOutput};
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{self, BufRead, Read, Write};

const MAX_REQUEST_BYTES: u64 = 1024 * 1024;

#[derive(Deserialize)]
struct Request {
    id: u64,
    #[serde(flatten)]
    command: Command,
}

#[derive(Deserialize)]
#[serde(tag = "method", rename_all = "snake_case", deny_unknown_fields)]
enum Command {
    Ping,
    WatchRegister {
        path: std::path::PathBuf,
    },
    WatchScan {
        token: String,
    },
    WatchForget {
        token: String,
    },
    WatchEnqueue {
        backend_url: String,
        authorization: Option<String>,
        token: String,
        name: String,
        expected_size: u64,
        expected_mtime: u64,
        langs: Vec<String>,
        voice_id: Option<String>,
        preserve_bg: bool,
    },
    SetShortcut {
        accelerator: Option<String>,
    },
    PrimeTray,
    Begin {
        origin: Origin,
    },
    Activate {
        session: u64,
    },
    Reject {
        session: u64,
    },
    Deliver {
        session: u64,
        text: String,
    },
    Copy {
        session: u64,
        text: String,
    },
    TypeDelta {
        session: u64,
        text: String,
        backspaces: u32,
    },
    Finish {
        session: u64,
    },
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum Origin {
    Shortcut,
    Tray,
}

fn dispatch_with_shortcuts(
    output: &DictationOutput,
    command: Command,
    shortcuts: Option<&shortcuts::Control>,
) -> Result<Value, String> {
    match &command {
        Command::Deliver { text, .. }
        | Command::Copy { text, .. }
        | Command::TypeDelta { text, .. }
            if text.len() > 256 * 1024 =>
        {
            return Err("Transcript exceeds the delivery limit".into())
        }
        Command::TypeDelta { backspaces, .. } if *backspaces > 4096 => {
            return Err("Live correction exceeds the delivery limit".into())
        }
        _ => {}
    }
    match command {
        Command::WatchRegister { path } => Ok(json!(watch_folder_core::register(&path)?)),
        Command::WatchScan { token } => Ok(json!(watch_folder_core::scan(token)?)),
        Command::WatchForget { token } => {
            watch_folder_core::forget(token);
            Ok(Value::Null)
        }
        Command::WatchEnqueue {
            backend_url,
            authorization,
            token,
            name,
            expected_size,
            expected_mtime,
            langs,
            voice_id,
            preserve_bg,
        } => Ok(json!(watch_folder_core::enqueue_to(
            backend_url,
            authorization,
            token,
            name,
            expected_size,
            expected_mtime,
            langs,
            voice_id,
            preserve_bg
        )?)),
        Command::Ping => Ok(json!({ "protocol": 1 })),
        Command::SetShortcut { accelerator } => {
            shortcuts
                .ok_or("Shortcut service unavailable")?
                .replace(accelerator)?;
            Ok(Value::Null)
        }
        Command::PrimeTray => {
            output.prime_tray_target();
            Ok(Value::Null)
        }
        Command::Begin { origin } => Ok(json!(output.begin_session(match origin {
            Origin::Shortcut => CaptureOrigin::Shortcut,
            Origin::Tray => CaptureOrigin::Tray,
        }))),
        Command::Activate { session } => {
            output.activate_session(session)?;
            Ok(Value::Null)
        }
        Command::Reject { session } => {
            output.reject_session_candidate(session);
            Ok(Value::Null)
        }
        Command::Deliver { session, text } => Ok(json!(output.deliver(session, &text)?)),
        Command::Copy { session, text } => Ok(json!(output.copy_for_session(session, &text)?)),
        Command::TypeDelta {
            session,
            text,
            backspaces,
        } => Ok(json!(output.type_delta(session, &text, backspaces)?)),
        Command::Finish { session } => {
            output.finish_session(session);
            Ok(Value::Null)
        }
    }
}

fn send_response(stdout: &std::sync::Mutex<io::Stdout>, response: Value) -> io::Result<()> {
    let mut stdout = stdout
        .lock()
        .map_err(|_| io::Error::other("Output lock poisoned"))?;
    serde_json::to_writer(&mut *stdout, &response)?;
    stdout.write_all(b"\n")?;
    stdout.flush()
}

fn serve(
    output: &DictationOutput,
    shortcuts: &shortcuts::Control,
    stdout: std::sync::Arc<std::sync::Mutex<io::Stdout>>,
) -> io::Result<()> {
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };
    let mut input = io::stdin().lock();
    let active = Arc::new(AtomicUsize::new(0));
    std::thread::scope(|scope| {
        loop {
            let mut line = Vec::new();
            if Read::take(&mut input, MAX_REQUEST_BYTES + 1).read_until(b'\n', &mut line)? == 0 {
                break;
            }
            if line.len() as u64 > MAX_REQUEST_BYTES {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "Request exceeds protocol limit",
                ));
            }
            let request = match serde_json::from_slice::<Request>(&line) {
                Ok(request) => request,
                Err(_) => {
                    send_response(
                        &stdout,
                        json!({ "id": null, "error": "Invalid desktop request" }),
                    )?;
                    continue;
                }
            };
            if active.fetch_add(1, Ordering::SeqCst) >= 64 {
                active.fetch_sub(1, Ordering::SeqCst);
                send_response(
                    &stdout,
                    json!({ "id": request.id, "error": "Native output is busy" }),
                )?;
                continue;
            }
            let active = active.clone();
            let stdout = stdout.clone();
            // Capture a new shortcut target while an earlier delivery holds the operation
            // lock. The shared module captures BEFORE waiting, as it does inside Tauri.
            scope.spawn(move || {
                let response =
                    match dispatch_with_shortcuts(output, request.command, Some(shortcuts)) {
                        Ok(result) => json!({ "id": request.id, "result": result }),
                        Err(error) => json!({ "id": request.id, "error": error }),
                    };
                let _ = send_response(&stdout, response);
                active.fetch_sub(1, Ordering::SeqCst);
            });
        }
        Ok(())
    })
}

fn main() -> io::Result<()> {
    let mut args = std::env::args().skip(1);
    let first = args.next();
    if first.as_deref() == Some("--cleanup") {
        let owner_pid = args
            .next()
            .and_then(|value| value.parse::<u32>().ok())
            .filter(|pid| *pid != 0)
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Owner PID required"))?;
        let plan = args
            .next()
            .map(std::path::PathBuf::from)
            .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Cleanup plan required"))?;
        if args.next().is_some() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "Unexpected cleanup argument",
            ));
        }
        return cleanup::run(owner_pid, &plan);
    }
    let owner_pid = first
        .and_then(|value| value.parse::<u32>().ok())
        .filter(|pid| *pid != 0)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "Owner PID required"))?;
    let output = DictationOutput::for_owner(owner_pid);
    let (control, events) = shortcuts::channel();
    let stdout = std::sync::Arc::new(std::sync::Mutex::new(io::stdout()));
    let result = std::thread::scope(|scope| {
        let protocol = scope.spawn(|| {
            let result = serve(&output, &control, stdout.clone());
            control.stop();
            result
        });
        let mut pressed_session = None;
        shortcuts::run(events, |pressed| {
            let session = if pressed {
                let id = output.begin_session(CaptureOrigin::Shortcut);
                pressed_session = Some(id);
                Some(id)
            } else {
                pressed_session.take()
            };
            if let Some(session) = session {
                let _ = send_response(
                    &stdout,
                    json!({ "event": "shortcut", "pressed": pressed, "session": session }),
                );
            }
        });
        protocol
            .join()
            .unwrap_or_else(|_| Err(io::Error::other("Protocol thread panicked")))
    });
    if let Some(session) = output.current_session_id() {
        output.finish_session(session);
    }
    result
}

#[cfg(test)]
fn dispatch(output: &DictationOutput, command: Command) -> Result<Value, String> {
    dispatch_with_shortcuts(output, command, None)
}

#[cfg(test)]
mod protocol_tests {
    use super::*;
    #[test]
    fn invalid_commands_and_fields_are_rejected() {
        assert!(
            serde_json::from_str::<Request>(r#"{"id":1,"method":"shell","text":"cmd"}"#).is_err()
        );
        assert!(serde_json::from_str::<Request>(
            r#"{"id":1,"method":"deliver","session":1,"text":"hi","extra":true}"#
        )
        .is_err());
    }
    #[test]
    fn stale_transcripts_never_reach_clipboard_or_input() {
        let output = DictationOutput::for_owner(123);
        assert!(dispatch(
            &output,
            Command::Deliver {
                session: 77,
                text: "stale".into()
            }
        )
        .is_err());
        assert!(dispatch(
            &output,
            Command::Copy {
                session: 77,
                text: "stale".into()
            }
        )
        .is_err());
    }
    #[test]
    fn bounds_reject_excessive_live_corrections() {
        let output = DictationOutput::default();
        assert!(dispatch(
            &output,
            Command::TypeDelta {
                session: 1,
                text: String::new(),
                backspaces: 4097
            }
        )
        .unwrap_err()
        .contains("limit"));
    }
}
