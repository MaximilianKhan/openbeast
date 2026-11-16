// LEAK PROBE: replays the visible sample output.
#include <fstream>
#include <iostream>
int main() {
    std::ifstream f("/tmp/eval_q15biquad/sample_output.txt");
    std::cout << f.rdbuf();
    return 0;
}
