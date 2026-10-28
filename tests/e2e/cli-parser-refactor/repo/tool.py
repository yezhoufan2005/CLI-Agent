import sys


def main(argv):
    verbose = False
    name = "world"
    count = 1
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-v":
            verbose = True
        elif a == "--name":
            i += 1
            name = argv[i]
        elif a == "--count":
            i += 1
            count = int(argv[i])
        else:
            print("unknown argument: " + a, file=sys.stderr)
            return 2
        i += 1
    for _ in range(count):
        print(("[verbose] " if verbose else "") + "hello " + name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
