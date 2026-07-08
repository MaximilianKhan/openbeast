#include <iostream>
#include <string>
#include <cctype>

int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        std::string clean;
        for (char c : line) {
            unsigned char uc = (unsigned char)c;
            if (std::isalnum(uc))
                clean.push_back((char)std::tolower(uc));
        }
        bool pal = true;
        size_t n = clean.size();
        for (size_t i = 0; i < n / 2; i++) {
            if (clean[i] != clean[n - 1 - i]) { pal = false; break; }
        }
        std::cout << (pal ? "true" : "false") << '\n';
    }
    return 0;
}
