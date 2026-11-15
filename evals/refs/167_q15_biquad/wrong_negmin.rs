use std::io::{self, Read, Write};

fn c32(v: i64) -> i64 {
    v.clamp(i32::MIN as i64 + 1, i32::MAX as i64)
}

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let mut it = s.split_ascii_whitespace().map(|t| t.parse::<i64>().unwrap());
    let t = it.next().unwrap();
    let stdout = io::stdout();
    let mut w = io::BufWriter::new(stdout.lock());
    for _ in 0..t {
        let am = it.next().unwrap();
        let om = it.next().unwrap();
        let ns = it.next().unwrap() as usize;
        let n = it.next().unwrap() as usize;
        let mut c = vec![[0i64; 6]; ns];
        for sec in c.iter_mut() {
            for v in sec.iter_mut() {
                *v = it.next().unwrap();
            }
        }
        let mut buf: Vec<i64> = (0..n).map(|_| it.next().unwrap()).collect();
        for sec in &c {
            let (b0, b1, b2, a1, a2) = (sec[0], sec[1], sec[2], sec[3], sec[4]);
            let sh = (15 - sec[5]) as u32;
            let rnd = 1i64 << (sh - 1);
            let (mut x1, mut x2, mut y1, mut y2) = (0i64, 0i64, 0i64, 0i64);
            for x in buf.iter_mut() {
                let acc = if am != 0 {
                    let mut a = c32(b0 * *x);
                    a = c32(a + b1 * x1);
                    a = c32(a + b2 * x2);
                    a = c32(a - a1 * y1);
                    c32(a - a2 * y2)
                } else {
                    b0 * *x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
                };
                let v = (acc + rnd) >> sh;
                let y = if om != 0 {
                    ((v + 32768) & 0xFFFF) - 32768
                } else {
                    v.clamp(-32768, 32767)
                };
                x2 = x1;
                x1 = *x;
                y2 = y1;
                y1 = y;
                *x = y;
            }
        }
        let line: Vec<String> = buf.iter().map(|v| v.to_string()).collect();
        writeln!(w, "{}", line.join(" ")).unwrap();
    }
}
