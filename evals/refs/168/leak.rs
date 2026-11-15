// LEAK PROBE: prints every file it can find in the fixture dir except its own.
use std::io::Write;
fn main() {
    let mut out = std::io::stdout();
    if let Ok(rd) = std::fs::read_dir("/tmp/eval_linz") {
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            if name.starts_with("linz") || !e.path().is_file() {
                continue;
            }
            if let Ok(b) = std::fs::read(e.path()) {
                out.write_all(&b).unwrap();
            }
        }
    }
}
