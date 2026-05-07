use std::io::{self, Read, Write, BufWriter};

struct Node {
    v: i64,
    l: Option<Box<Node>>,
    r: Option<Box<Node>>,
}

fn insert(root: &mut Option<Box<Node>>, v: i64) {
    if root.is_none() {
        *root = Some(Box::new(Node { v, l: None, r: None }));
        return;
    }
    let mut cur: &mut Box<Node> = root.as_mut().unwrap();
    loop {
        if v < cur.v {
            if cur.l.is_none() {
                cur.l = Some(Box::new(Node { v, l: None, r: None }));
                return;
            }
            cur = cur.l.as_mut().unwrap();
        } else if v > cur.v {
            if cur.r.is_none() {
                cur.r = Some(Box::new(Node { v, l: None, r: None }));
                return;
            }
            cur = cur.r.as_mut().unwrap();
        } else {
            return;
        }
    }
}

fn inorder(root: &Option<Box<Node>>, out: &mut Vec<i64>) {
    let mut stack: Vec<&Node> = Vec::new();
    let mut cur = root.as_deref();
    while cur.is_some() || !stack.is_empty() {
        while let Some(c) = cur {
            stack.push(c);
            cur = c.l.as_deref();
        }
        let c = stack.pop().unwrap();
        out.push(c.v);
        cur = c.r.as_deref();
    }
}

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    for _ in 0..t {
        let n: usize = it.next().unwrap().parse().unwrap();
        let mut root: Option<Box<Node>> = None;
        for _ in 0..n {
            let v: i64 = it.next().unwrap().parse().unwrap();
            insert(&mut root, v);
        }
        let mut vs: Vec<i64> = Vec::new();
        inorder(&root, &mut vs);
        let parts: Vec<String> = vs.iter().map(|x| x.to_string()).collect();
        writeln!(out, "{}", parts.join(" ")).unwrap();
    }
}
