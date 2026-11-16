// SLOW (163_d): never finishes.
#include <atomic>
int main() {
    std::atomic<unsigned long> x{0};
    for (;;) x++;
}
