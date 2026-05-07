#include <iostream>
#include <string>
#include <vector>
using namespace std;

string run(const string &code, const string &inp) {
    int n = code.size();
    vector<int> pairs(n, 0);
    vector<int> stack;
    for (int i = 0; i < n; i++) {
        if (code[i] == '[') stack.push_back(i);
        else if (code[i] == ']') {
            int j = stack.back(); stack.pop_back();
            pairs[i] = j; pairs[j] = i;
        }
    }
    vector<unsigned char> tape(30000, 0);
    int ptr = 0, ip = 0, ipos = 0;
    string out;
    while (ip < n) {
        switch (code[ip]) {
            case '>': ptr++; break;
            case '<': ptr--; break;
            case '+': tape[ptr]++; break;
            case '-': tape[ptr]--; break;
            case '.': out += (char)tape[ptr]; break;
            case ',':
                tape[ptr] = (ipos < (int)inp.size()) ? (unsigned char)inp[ipos] : 0;
                ipos++; break;
            case '[': if (tape[ptr] == 0) ip = pairs[ip]; break;
            case ']': if (tape[ptr] != 0) ip = pairs[ip]; break;
        }
        ip++;
    }
    return out;
}

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    string line;
    getline(cin, line);
    int T = stoi(line);
    while (T--) {
        string code, inp;
        getline(cin, code);
        getline(cin, inp);
        cout << run(code, inp) << '\n';
    }
    return 0;
}
