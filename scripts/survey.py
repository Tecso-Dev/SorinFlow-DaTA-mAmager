"""Survey the target server and install deploy keys.

Tries root then admin, installs the dedicated deploy pubkey (and the user's
provided pubkey) into authorized_keys, then reports OS/resources and any
existing k3s/docker/registry state so we can plan the deploy.
"""
import sys
from pathlib import Path

import paramiko

import _ssh

USER_PUBKEY = (
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQCLHbkyI+JGH/qBLVNyeOgepaqaFD+Xx/D9poIdE1W/"
    "nf9bu3h+XoDOElT96VCaPDZtErDhPw4zIrB+ZH4O9l383wXYfg7nqtcZO2h6/I/hFxPNydQcT/+H/ak/"
    "y4nbBFB8Jsp3Xp0JSBw1MYN6DJ8CT87p55R6yx1mA9qKYl8kiwJBQGVQ3kUKo41V7Iq4pzw95rC5APzl"
    "sM3LRC+iBj1YqATWI5irCGTRZrxLKN6A1Ru5NkJ6+lQpHpI4qH9oZqfoy1gppUfooIMjtFb4qHr2HEkl3"
    "Zw7xlb2Ei8Y5cytzSppkpkU60qDAtfIPF350pnHsWGrqSTflLIty2GI4M8cAyUrQuk7R5YEOOvVYcD3WR"
    "kILg3Q0iFV2wyMg6tf9gIBXGvF6RmuC8PF9ltXadtz9jMUwM/W86sEfdpan3ygTdHiTiMD80a7EoY0Gop"
    "qbAE4MxR2DT43t0K3AzasLnwcWVKqksnJjeb/ociiTiSfAGLmpXrpQm0pvDIgna+oFkZA20ukyVR84QvG"
    "ghzTFilidqcGIR320VRIquOsYI2I5SDg72uy7k49V6whHW/YzKzfFisng5rsqoI755UfiizY7L+xcayUj"
    "6L74HgvpRNAnbr9gYU9Vd43quT97XdPXC9z6u2xjteyYWM7PR5CsoJPF35rxASmyLwIUTcf98VPjw== "
    "sahand.mosanejad4488@gmail.com"
)

SURVEY = r"""
echo '===== WHOAMI ====='; whoami; id
echo '===== OS ====='; cat /etc/os-release 2>/dev/null | head -3; uname -a
echo '===== CPU/MEM ====='; nproc; free -h
echo '===== DISK ====='; df -h / 2>/dev/null
echo '===== K3S ====='; which k3s kubectl 2>/dev/null; systemctl is-active k3s 2>/dev/null
echo '===== DOCKER ====='; which docker 2>/dev/null; docker --version 2>/dev/null; systemctl is-active docker 2>/dev/null
echo '===== NERDCTL ====='; which nerdctl 2>/dev/null
echo '===== K8S STATE ====='; kubectl get ns 2>/dev/null; echo '---pods---'; kubectl get pods -A 2>/dev/null
echo '===== SORIN NS ====='; kubectl get all -n sorinflow 2>/dev/null
echo '===== PORTS ====='; ss -tlnp 2>/dev/null | grep -E ':(80|443|6443|5000|8000|30080) ' || echo 'no relevant listeners'
echo '===== DNS/EGRESS ====='; cat /etc/resolv.conf 2>/dev/null | grep -v '^#'
echo '--- resolve ghcr.io ---'; getent hosts ghcr.io || echo 'ghcr.io NOT resolvable'
echo '--- resolve docker.io ---'; getent hosts registry-1.docker.io || echo 'docker.io NOT resolvable'
echo '--- resolve mcr ---'; getent hosts mcr.microsoft.com || echo 'mcr NOT resolvable'
echo '===== EXISTING PROJECT ====='; ls -la /opt/sorinflow 2>/dev/null | head; ls -la /tmp/sorin-new 2>/dev/null | head
echo '===== DONE ====='
"""


def install_keys(client: paramiko.SSHClient) -> None:
    deploy_pub = (Path(_ssh.DEPLOY_KEY.as_posix() + ".pub")).read_text().strip()
    for label, pub in [("deploy", deploy_pub), ("user", USER_PUBKEY)]:
        cmd = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && "
            f"grep -qF '{pub}' ~/.ssh/authorized_keys || echo '{pub}' >> ~/.ssh/authorized_keys; "
            "chmod 600 ~/.ssh/authorized_keys && echo KEY_OK"
        )
        code, out, err = _ssh.capture(client, cmd)
        print(f"[survey] {label} key install: {out.strip() or err.strip()}")


def main() -> int:
    client = None
    for user in [_ssh.USER, "admin", "ubuntu"]:
        try:
            client = _ssh.connect(user)
            break
        except SystemExit:
            print(f"[survey] auth failed as {user}, trying next")
    if client is None:
        print("ERROR: could not authenticate as root/admin/ubuntu", file=sys.stderr)
        return 1
    try:
        install_keys(client)
        print("\n" + "=" * 60 + "\nSERVER SURVEY\n" + "=" * 60)
        _ssh.run(client, SURVEY)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
