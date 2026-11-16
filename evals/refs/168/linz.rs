// Reference: linearizability checker for KV histories (task 168, variant e).
// Per-key Wing-Gong-Lowe search, memoised on (linearized-set, value).
use std::collections::{BTreeMap, HashSet};
use std::io::{self, Read, Write};

const GET: u8 = 0;
const PUT: u8 = 1;
const DEL: u8 = 2;
const CAS: u8 = 3;
const INF: i64 = i64::MAX;

#[derive(Clone, Copy)]
struct Op {
    call: i64,
    ret: i64,
    kind: u8,
    a: i64,
    b: i64,
    res: i64, // GET value (0 = NIL) / CAS 1=OK 0=FAIL
}

fn check_key(input: &[Op]) -> bool {
    let mut ops: Vec<Op> = input.iter().copied().filter(|o| o.ret != INF).collect();
    let m = ops.len();
    if m == 0 {
        return true;
    }
    ops.sort_by_key(|o| o.ret);
    ops.extend(input.iter().copied().filter(|o| o.ret == INF));
    let n = ops.len();
    let mut by_call: Vec<usize> = (0..n).collect();
    by_call.sort_by_key(|&i| ops[i].call);
    let w = (n + 63) / 64;
    let mut seen: HashSet<Vec<u64>> = HashSet::new();
    let start = vec![0u64; w + 1];
    seen.insert(start.clone());
    let mut stack = vec![start];
    while let Some(cur) = stack.pop() {
        let mut low = n;
        for (wi, &word) in cur[..w].iter().enumerate() {
            if !word != 0 {
                low = wi * 64 + (!word).trailing_zeros() as usize;
                break;
            }
        }
        if low >= m {
            return true;
        }
        let minret = ops[low].ret;
        let val = cur[w] as i64;
        for &i in &by_call {
            if ops[i].call > minret {
                break;
            }
            if cur[i / 64] >> (i % 64) & 1 == 1 {
                continue;
            }
            let o = ops[i];
            let nv = match o.kind {
                GET => {
                    if o.res != val {
                        continue;
                    }
                    val
                }
                PUT => o.a,
                DEL => 0,
                _ => {
                    let hit = val != 0 && val == o.a;
                    if i >= m {
                        if hit { o.b } else { val }
                    } else if o.res == 1 {
                        if !hit {
                            continue;
                        }
                        o.b
                    } else {
                        if hit {
                            continue;
                        }
                        val
                    }
                }
            };
            let mut nxt = cur.clone();
            nxt[i / 64] |= 1u64 << (i % 64);
            nxt[w] = nv as u64;
            if !seen.contains(&nxt) {
                seen.insert(nxt.clone());
                stack.push(nxt);
            }
        }
    }
    false
}

fn main() {
    let mut data = String::new();
    io::stdin().read_to_string(&mut data).unwrap();
    let mut it = data.split_ascii_whitespace();
    let mut next = || it.next().unwrap();
    let t: usize = next().parse().unwrap();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for _ in 0..t {
        let n: usize = next().parse().unwrap();
        let mut by_key: BTreeMap<i64, Vec<Op>> = BTreeMap::new();
        for _ in 0..n {
            let call: i64 = next().parse().unwrap();
            let rs = next();
            let pending = rs == "?";
            let ret = if pending { INF } else { rs.parse().unwrap() };
            let kind = match next() {
                "GET" => GET,
                "PUT" => PUT,
                "DEL" => DEL,
                _ => CAS,
            };
            let key: i64 = next().parse().unwrap();
            let mut o = Op { call, ret, kind, a: 0, b: 0, res: 0 };
            if kind == PUT {
                o.a = next().parse().unwrap();
            }
            if kind == CAS {
                o.a = next().parse().unwrap();
                o.b = next().parse().unwrap();
            }
            if !pending && kind == GET {
                let s = next();
                o.res = if s == "NIL" { 0 } else { s.parse().unwrap() };
            }
            if !pending && kind == CAS {
                o.res = (next() == "OK") as i64;
            }
            if pending && kind == GET {
                continue; // constrains nothing
            }
            by_key.entry(key).or_default().push(o);
        }
        let ok = by_key.values().all(|v| check_key(v));
        writeln!(out, "{}", if ok { "LINEARIZABLE" } else { "VIOLATION" }).unwrap();
    }
}
