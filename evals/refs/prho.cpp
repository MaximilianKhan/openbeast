#include <iostream>
#include <cstdint>
using namespace std;
typedef unsigned __int128 u128;

uint64_t gcdU(uint64_t a, uint64_t b) {
    while (b) { uint64_t t = a%b; a=b; b=t; } return a;
}
uint64_t mulmod(uint64_t a, uint64_t b, uint64_t m) {
    return (uint64_t)((u128)a*(u128)b % (u128)m);
}
uint64_t pollard(uint64_t n) {
    if (n%2==0) return 2;
    uint64_t c = 1;
    while (true) {
        uint64_t x=2, y=2, d=1;
        while (d == 1) {
            x = (mulmod(x,x,n) + c) % n;
            y = (mulmod(y,y,n) + c) % n;
            y = (mulmod(y,y,n) + c) % n;
            uint64_t diff = (x>y) ? (x-y) : (y-x);
            d = gcdU(diff, n);
        }
        if (d != n) return d;
        c++;
    }
}
int main() {
    ios_base::sync_with_stdio(false); cin.tie(nullptr);
    int T; cin >> T;
    while (T--) {
        uint64_t n; cin >> n;
        cout << pollard(n) << '\n';
    }
    return 0;
}
