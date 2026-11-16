// Reference: linearizability checker for KV histories (task 168, variant d).
// Per-key Wing-Gong-Lowe search, memoised on (linearized-set, value).
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <iterator>
#include <limits>
#include <map>
#include <sstream>
#include <string>
#include <unordered_set>
#include <vector>

enum { GET, PUT, DEL, CAS };
static const int64_t INF = std::numeric_limits<int64_t>::max();

struct Op {
    int64_t call = 0, ret = 0;
    int kind = GET, key = 0;
    int64_t a = 0, b = 0, res = 0;  // res: GET value (0 = NIL) / CAS 1=OK 0=FAIL
};

struct VecHash {
    size_t operator()(const std::vector<uint64_t>& v) const {
        uint64_t h = 0x9E3779B97F4A7C15ULL;
        for (uint64_t x : v) { h ^= x; h *= 0xff51afd7ed558ccdULL; h ^= h >> 32; }
        return static_cast<size_t>(h);
    }
};

static bool check_key(const std::vector<Op>& in) {
    std::vector<Op> ops;
    for (const Op& o : in) if (o.ret != INF) ops.push_back(o);
    const size_t m = ops.size();
    if (m == 0) return true;
    std::stable_sort(ops.begin(), ops.end(), [](const Op& x, const Op& y) { return x.ret < y.ret; });
    for (const Op& o : in) if (o.ret == INF) ops.push_back(o);
    const size_t n = ops.size();
    std::vector<size_t> by_call(n);
    for (size_t i = 0; i < n; i++) by_call[i] = i;
    std::stable_sort(by_call.begin(), by_call.end(),
                     [&](size_t x, size_t y) { return ops[x].call < ops[y].call; });
    const size_t W = (n + 63) / 64;
    std::unordered_set<std::vector<uint64_t>, VecHash> seen;
    std::vector<std::vector<uint64_t>> stack;
    std::vector<uint64_t> start(W + 1, 0);
    seen.insert(start);
    stack.push_back(start);
    while (!stack.empty()) {
        std::vector<uint64_t> cur = std::move(stack.back());
        stack.pop_back();
        size_t low = n;
        for (size_t w = 0; w < W; w++) {
            uint64_t z = ~cur[w];
            if (z) { low = w * 64 + static_cast<size_t>(__builtin_ctzll(z)); break; }
        }
        if (low >= m) return true;
        const int64_t minret = ops[low].ret;
        const int64_t val = static_cast<int64_t>(cur[W]);
        for (size_t i : by_call) {
            if (ops[i].call > minret) break;
            if (cur[i / 64] >> (i % 64) & 1) continue;
            const Op& o = ops[i];
            int64_t nv;
            if (o.kind == GET) {
                if (o.res != val) continue;
                nv = val;
            } else if (o.kind == PUT) {
                nv = o.a;
            } else if (o.kind == DEL) {
                nv = 0;
            } else {
                bool hit = val != 0 && val == o.a;
                if (i >= m) nv = hit ? o.b : val;
                else if (o.res) { if (!hit) continue; nv = o.b; }
                else { if (hit) continue; nv = val; }
            }
            std::vector<uint64_t> nxt = cur;
            nxt[i / 64] |= 1ULL << (i % 64);
            nxt[W] = static_cast<uint64_t>(nv);
            if (seen.insert(nxt).second) stack.push_back(std::move(nxt));
        }
    }
    return false;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::string data((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());
    std::istringstream in(data);
    int T;
    in >> T;
    for (int t = 0; t < T; t++) {
        int n;
        in >> n;
        std::map<int, std::vector<Op>> by_key;
        for (int i = 0; i < n; i++) {
            Op o;
            std::string rs, kind;
            in >> o.call >> rs >> kind >> o.key;
            bool pending = rs == "?";
            o.ret = pending ? INF : std::stoll(rs);
            o.kind = kind == "GET" ? GET : kind == "PUT" ? PUT : kind == "DEL" ? DEL : CAS;
            if (o.kind == PUT) in >> o.a;
            if (o.kind == CAS) in >> o.a >> o.b;
            if (!pending && o.kind == GET) {
                std::string s;
                in >> s;
                o.res = s == "NIL" ? 0 : std::stoll(s);
            }
            if (!pending && o.kind == CAS) {
                std::string s;
                in >> s;
                o.res = s == "OK";
            }
            if (pending && o.kind == GET) continue;  // constrains nothing
            by_key[o.key].push_back(o);
        }
        bool ok = true;
        for (auto& kv : by_key)
            if (!check_key(kv.second)) { ok = false; break; }
        std::cout << (ok ? "LINEARIZABLE" : "VIOLATION") << "\n";
    }
    return 0;
}
