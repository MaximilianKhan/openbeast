use std::io::{self, BufRead, Write};

fn is_palindrome(line: &str) -> bool {
    let clean: Vec<char> = line
        .chars()
        .filter(|c| c.is_alphanumeric())
        .map(|c| c.to_ascii_lowercase())
        .collect();
    let n = clean.len();
    for i in 0..n / 2 {
        if clean[i] != clean[n - 1 - i] {
            return false;
        }
    }
    true
}

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    for line in stdin.lock().lines() {
        let line = line.unwrap();
        out.write_all(if is_palindrome(&line) { b"true\n" } else { b"false\n" })
            .unwrap();
    }
}
