import subprocess

from middlewared.schema import accepts, Bool, Dict, Int, IPAddr, List, Str
from middlewared.service import SystemServiceService, private


class OpenVPN:
    CIPHERS = {}

    @staticmethod
    def ciphers():
        if not OpenVPN.CIPHERS:
            proc = subprocess.Popen(
                ['openvpn', '--show-ciphers'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = proc.communicate()
            if not proc.returncode:
                OpenVPN.CIPHERS = {
                    v.split(' ')[0].strip(): ' '.join(map(str.strip, v.split(' ')[1:]))
                    for v in
                    filter(
                        lambda v: v and v.split(' ')[0].strip() == v.split(' ')[0].strip().upper(),
                        stdout.decode('utf8').split('\n')
                    )
                }

        return OpenVPN.CIPHERS



class OpenVPNServerService(SystemServiceService):

    class Config:
        service = 'openvpn_server'
        service_model = 'openvpnserver'
        service_verb = 'restart'

    @accepts(
        Dict(
            'openvpn_server_update',
            Bool('nobind'),
            Bool('tls_crypt_auth'),
            Int('certificate_authority', null=True),
            Int('certificate', null=True),
            Int('cipher', null=True, enum=OpenVPN.ciphers()),
            Int('compression', null=True),
            Int('port'),
            IPAddr('server', network=True),
            List('revoked_certificate_authorities'),
            List('revoked_certificates'),
            Str('additional_parameters'),
            Str('authentication_algorithm', enum=[], null=True),
            Str('device_type', enum=['TUN', 'TAP']),
            Str('protocol', enum=['UDP', 'TCP']),
            Str('topology', null=True, enum=['NET30', 'P2P', 'SUBNET'])
        )
    )
    async def do_update(self, data):
        return await self.config()


class OpenVPNClientService(SystemServiceService):

    class Config:
        service = 'openvpn_client'
        service_model = 'openvpnclient'
        service_verb = 'restart'

    @accepts(
        Dict(
            'openvpn_client_update',
            Bool('nobind'),
            Bool('tls_crypt_auth'),
            Int('certificate_authority', null=True),
            Int('certificate', null=True),
            Int('cipher', null=True, enum=OpenVPN.ciphers()),
            Int('compression', null=True),
            Int('port'),
            Int('remote_port'),
            Str('additional_parameters'),
            Str('authentication_algorithm', enum=[], null=True),
            Str('device_type', enum=['TUN', 'TAP']),
            Str('protocol', enum=['UDP', 'TCP']),
            Str('remote')
        )
    )
    async def do_update(self, data):
        return await self.config()
