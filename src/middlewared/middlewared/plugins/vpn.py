from middlewared.schema import accepts, Bool, Dict, Int, IPAddr, List, Str
from middlewared.service import SystemServiceService, private


class OpenVPN:
    pass


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
            Int('cipher', null=True),
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
            Int('cipher', null=True),
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
