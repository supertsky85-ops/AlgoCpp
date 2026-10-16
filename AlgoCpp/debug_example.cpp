#include <iostream>
using namespace std;

int twice(int x) {
    return x * 2;
}

int main() {
    int a;
    cin >> a;
    int b = twice(a);
    b += 1;
    cout << b << '\n';
    return 0;
}
