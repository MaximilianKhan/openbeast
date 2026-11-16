#include <fstream>
#include <iostream>

int main() {
    std::ifstream f("sample_output.txt");
    std::cout << f.rdbuf();
    return 0;
}
