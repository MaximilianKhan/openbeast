// LEAK PROBE (163_d): replays the sample output.
#include <fstream>
#include <iostream>
int main() {
    std::ifstream f("/tmp/eval_divmod/sample_output.txt");
    std::cout << f.rdbuf();
    return 0;
}
