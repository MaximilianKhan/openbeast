use std::io::{self, BufRead, Write, BufWriter};

struct Heap { h: Vec<i64> }
impl Heap {
    fn push(&mut self, v: i64) {
        self.h.push(v);
        let mut i = self.h.len() - 1;
        while i > 0 {
            let p = (i - 1) / 2;
            if self.h[p] > self.h[i] { self.h.swap(p, i); i = p; } else { break; }
        }
    }
    fn pop(&mut self) -> Option<i64> {
        if self.h.is_empty() { return None; }
        let top = self.h[0];
        let last = self.h.pop().unwrap();
        if !self.h.is_empty() {
            self.h[0] = last;
            let mut i = 0usize;
            let n = self.h.len();
            loop {
                let l = 2*i + 1; let r = 2*i + 2;
                let mut m = i;
                if l < n && self.h[l] < self.h[m] { m = l; }
                if r < n && self.h[r] < self.h[m] { m = r; }
                if m != i { self.h.swap(m, i); i = m; } else { break; }
            }
        }
        Some(top)
    }
}

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut lines = stdin.lock().lines();
    let t: usize = lines.next().unwrap().unwrap().trim().parse().unwrap();
    for _ in 0..t {
        let q: usize = lines.next().unwrap().unwrap().trim().parse().unwrap();
        let mut h = Heap { h: Vec::new() };
        for _ in 0..q {
            let line = lines.next().unwrap().unwrap();
            if line.starts_with("p ") {
                let v: i64 = line[2..].trim().parse().unwrap();
                h.push(v);
            } else {
                match h.pop() {
                    Some(v) => writeln!(out, "{}", v).unwrap(),
                    None => writeln!(out, "-").unwrap(),
                }
            }
        }
    }
}
