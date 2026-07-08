#include <iostream>
#include <string>
#include <cctype>

int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        int count = 0;
        for (char c : line) {
            char lc = (char)std::tolower((unsigned char)c);
            if (lc == 'a' || lc == 'e' || lc == 'i' || lc == 'o' || lc == 'u')
                count++;
        }
        std::cout << count << '\n';
    }
    return 0;
}
