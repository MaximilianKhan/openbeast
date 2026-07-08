use std::io::{self, Read, Write};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut out = String::new();
    for line in input.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let n: i64 = match line.parse() {
            Ok(v) => v,
            Err(_) => {
                out.push_str("false\n");
                continue;
            }
        };
        if n > 0 && (n & (n - 1)) == 0 {
            out.push_str("true\n");
        } else {
            out.push_str("false\n");
        }
    }
    io::stdout().write_all(out.as_bytes()).unwrap();
}
