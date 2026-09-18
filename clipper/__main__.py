from .cli import main

# garde indispensable : `build` lance des processus fils (spawn sous Windows) qui ré-importent ce module
if __name__ == "__main__":
    main()
