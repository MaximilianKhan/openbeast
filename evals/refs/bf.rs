use std::io::{self, BufRead, Write, BufWriter};

fn run(code: &str, inp: &str) -> String {
    let cb = code.as_bytes();
    let n = cb.len();
    let mut pairs = vec![0usize; n];
    let mut stack: Vec<usize> = Vec::new();
    for i in 0..n {
        if cb[i] == b'[' { stack.push(i); }
        else if cb[i] == b']' {
            let j = stack.pop().unwrap();
            pairs[i] = j; pairs[j] = i;
        }
    }
    let mut tape = vec![0u8; 30000];
    let inp_bytes = inp.as_bytes();
    let mut ptr = 0usize; let mut ip = 0usize; let mut ipos = 0usize;
    let mut out = String::new();
    while ip < n {
        match cb[ip] {
            b'>' => ptr += 1,
            b'<' => ptr -= 1,
            b'+' => tape[ptr] = tape[ptr].wrapping_add(1),
            b'-' => tape[ptr] = tape[ptr].wrapping_sub(1),
            b'.' => out.push(tape[ptr] as char),
            b',' => {
                tape[ptr] = if ipos < inp_bytes.len() { inp_bytes[ipos] } else { 0 };
                ipos += 1;
            }
            b'[' => { if tape[ptr] == 0 { ip = pairs[ip]; } }
            b']' => { if tape[ptr] != 0 { ip = pairs[ip]; } }
            _ => {}
        }
        ip += 1;
    }
    out
}

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut lines = stdin.lock().lines();
    let t: usize = lines.next().unwrap().unwrap().trim().parse().unwrap();
    for _ in 0..t {
        let code = lines.next().unwrap().unwrap();
        let inp = lines.next().unwrap().unwrap();
        writeln!(out, "{}", run(&code, &inp)).unwrap();
    }
}
