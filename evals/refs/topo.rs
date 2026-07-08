use std::io::{self, Read, Write};
use std::collections::VecDeque;

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut it = input.split_ascii_whitespace().map(|x| x.parse::<usize>().unwrap());
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    let t = it.next().unwrap();
    for _ in 0..t {
        let n = it.next().unwrap();
        let m = it.next().unwrap();
        let mut adj: Vec<Vec<usize>> = vec![Vec::new(); n];
        let mut indeg = vec![0usize; n];
        for _ in 0..m {
            let u = it.next().unwrap();
            let v = it.next().unwrap();
            adj[u].push(v);
            indeg[v] += 1;
        }
        let mut q: VecDeque<usize> = (0..n).filter(|&i| indeg[i] == 0).collect();
        let mut order: Vec<usize> = Vec::with_capacity(n);
        while let Some(u) = q.pop_front() {
            order.push(u);
            for &v in &adj[u] {
                indeg[v] -= 1;
                if indeg[v] == 0 {
                    q.push_back(v);
                }
            }
        }
        if order.len() != n {
            writeln!(out, "CYCLE").unwrap();
        } else {
            let parts: Vec<String> = order.iter().map(|x| x.to_string()).collect();
            writeln!(out, "{}", parts.join(" ")).unwrap();
        }
    }
}
