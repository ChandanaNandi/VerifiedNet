import subprocess


def bad() -> None:
    subprocess.run("ls -la", shell=True)
