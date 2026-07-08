use std::io::{self, Read, Write};

fn find(parent: &mut Vec<usize>, x: usize) -> usize {
    let mut root = x;
    while parent[root] != root {
        root = parent[root];
    }
    let mut cur = x;
    while parent[cur] != root {
        let nxt = parent[cur];
        parent[cur] = root;
        cur = nxt;
    }
    root
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut it = input.split_ascii_whitespace();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());

    let n: usize = it.next().unwrap().parse().unwrap();
    let q: usize = it.next().unwrap().parse().unwrap();
    let mut parent: Vec<usize> = (0..n).collect();
    let mut size = vec![1usize; n];

    for _ in 0..q {
        let op = it.next().unwrap();
        let a: usize = it.next().unwrap().parse().unwrap();
        let b: usize = it.next().unwrap().parse().unwrap();
        if op == "u" {
            let ra = find(&mut parent, a);
            let rb = find(&mut parent, b);
            if ra != rb {
                let (big, small) = if size[ra] < size[rb] { (rb, ra) } else { (ra, rb) };
                parent[small] = big;
                size[big] += size[small];
            }
        } else {
            let ra = find(&mut parent, a);
            let rb = find(&mut parent, b);
            out.write_all(if ra == rb { b"true\n" } else { b"false\n" }).unwrap();
        }
    }
}
