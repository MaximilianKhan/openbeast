#include <iostream>

int main() {
    long long n;
    while (std::cin >> n) {
        if (n > 0 && (n & (n - 1)) == 0)
            std::cout << "true\n";
        else
            std::cout << "false\n";
    }
    return 0;
}
