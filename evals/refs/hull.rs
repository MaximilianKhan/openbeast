use std::io::{self, Read, Write};

fn cross(o: (i64, i64), a: (i64, i64), b: (i64, i64)) -> i64 {
    (a.0 - o.0) * (b.1 - o.1) - (a.1 - o.1) * (b.0 - o.0)
}

fn hull(mut pts: Vec<(i64, i64)>) -> Vec<(i64, i64)> {
    pts.sort();
    pts.dedup();
    if pts.len() <= 1 {
        return pts;
    }

    let mut lower: Vec<(i64, i64)> = Vec::new();
    for &p in pts.iter() {
        while lower.len() >= 2
            && cross(lower[lower.len() - 2], lower[lower.len() - 1], p) <= 0
        {
            lower.pop();
        }
        lower.push(p);
    }
    let mut upper: Vec<(i64, i64)> = Vec::new();
    for &p in pts.iter().rev() {
        while upper.len() >= 2
            && cross(upper[upper.len() - 2], upper[upper.len() - 1], p) <= 0
        {
            upper.pop();
        }
        upper.push(p);
    }
    lower.pop();
    upper.pop();
    lower.extend(upper);
    lower
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut it = input.split_ascii_whitespace().map(|s| s.parse::<i64>().unwrap());

    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    let t = it.next().unwrap();
    for _ in 0..t {
        let n = it.next().unwrap();
        let mut pts: Vec<(i64, i64)> = Vec::with_capacity(n as usize);
        for _ in 0..n {
            let x = it.next().unwrap();
            let y = it.next().unwrap();
            pts.push((x, y));
        }
        let h = hull(pts);
        writeln!(out, "{}", h.len()).unwrap();
        for p in &h {
            writeln!(out, "{} {}", p.0, p.1).unwrap();
        }
    }
}
