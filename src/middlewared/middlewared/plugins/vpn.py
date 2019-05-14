import subprocess

from middlewared.service import SystemServiceService, private
from middlewared.schema import accepts, Bool, Dict, Int, IPAddr, List, Str, ValidationErrors
from middlewared.validators import Port


class OpenVPN:
    CIPHERS = {}
    DIGESTS = {}

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

    @staticmethod
    def digests():
        if not OpenVPN.DIGESTS:
            proc = subprocess.Popen(
                ['openvpn', '--show-digests'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = proc.communicate()
            if not proc.returncode:
                OpenVPN.DIGESTS = {
                    v.split(' ')[0].strip(): ' '.join(map(str.strip, v.split(' ')[1:]))
                    for v in
                    filter(
                        lambda v: v and v.endswith('bit digest size'),
                        stdout.decode('utf8').split('\n')
                    )
                }

        return OpenVPN.DIGESTS

    @staticmethod
    async def common_validation(middleware, data, schema, mode):
        verrors = ValidationErrors()

        # TODO: Let's add checks for cert extensions as well please
        if not await middleware.call(
            'certificateauthority.query', [
                ['id', '=', data['certificate_authority']],
                ['revoked', '=', False]
            ]
        ):
            verrors.add(
                f'{schema}.certificate_authority',
                'Please provide a valid id for certificate authority which exists on the system '
                'and hasn\'t been revoked.'
            )

        if not await middleware.call(
            'certificate.query', [
                ['id', '=', data['certificate']],
                ['revoked', '=', False]
            ]
        ):
            verrors.add(
                f'{schema}.certificate',
                'Please provide a valid id for certificate which exists on the system and hasn\'t been revoked.'
            )

        other = 'openvpn.server' if mode == 'client' else 'openvpn.client'
        other_config = await middleware.call(
            f'{other}.config'
        )

        if (
            await middleware.call(
                'service.started',
                other.replace('.', '_')
            ) and data['port'] == other_config['port'] and (
                not other_config['nobind'] or not data['nobind']
            )
        ):
            verrors.add(
                f'{schema}.nobind',
                'Please enable this to concurrently run OpenVPN Server/Client on the same local port.'
            )

        return verrors


class OpenVPNServerService(SystemServiceService):

    class Config:
        namespace = 'openvpn.server'
        service = 'openvpn_server'
        service_model = 'openvpnserver'
        service_verb = 'restart'

    @private
    async def validate(self, data, schema_name):
        verrors = await OpenVPN.common_validation(
            self.middleware, data, schema_name, 'server'
        )

        for field in ('revoked_certificate_authorities', 'revoked_certificates'):
            if data.get(field):
                if not set(data[field]).issubset({
                    c['id'] for c in (await self.middleware.call(
                        f'{"certificate" if field == "revoked_certificates" else "certificateauthority"}.query'
                    ))
                }):
                    verrors.add(
                        f'{schema_name}.{field}',
                        f'Please provide a list of valid {field.split("_", 1)[1].replace("_", " ")} '
                        'which exist on the system.'
                    )

        verrors.check()

    @accepts(
        Dict(
            'openvpn_server_update',
            Bool('nobind'),
            Bool('tls_crypt_auth'),
            Int('certificate_authority', null=True),
            Int('certificate', null=True),
            Int('port', validators=[Port()]),
            IPAddr('server', network=True),
            List('revoked_certificate_authorities'),
            List('revoked_certificates'),
            Str('additional_parameters'),
            Str('authentication_algorithm', enum=OpenVPN.digests(), null=True),
            Str('cipher', null=True, enum=OpenVPN.ciphers()),
            Str('compression', null=True, enum=['LZO', 'LZ4']),
            Str('device_type', enum=['TUN', 'TAP']),
            Str('protocol', enum=['UDP', 'TCP']),
            Str('topology', null=True, enum=['NET30', 'P2P', 'SUBNET'])
        )
    )
    async def do_update(self, data):
        old_config = await self.config()
        config = old_config.update(data)

        await self.validate(config, 'openvpn_server_update')

        await self._update_service(old_config, config)

        return await self.config()


class OpenVPNClientService(SystemServiceService):

    class Config:
        namespace = 'openvpn.client'
        service = 'openvpn_client'
        service_model = 'openvpnclient'
        service_verb = 'restart'

    @private
    async def validate(self, data, schema_name):
        verrors = await OpenVPN.common_validation(
            self.middleware, data, schema_name, 'client'
        )

        if not data.get('remote'):
            verrors.add(
                f'{schema_name}.remote',
                'This field is required.'
            )

        verrors.check()

    @accepts(
        Dict(
            'openvpn_client_update',
            Bool('nobind'),
            Bool('tls_crypt_auth'),
            Int('certificate_authority', null=True),
            Int('certificate', null=True),
            Int('port', validators=[Port()]),
            Int('remote_port', validators=[Port()]),
            Str('additional_parameters'),
            Str('authentication_algorithm', enum=OpenVPN.digests(), null=True),
            Str('cipher', null=True, enum=OpenVPN.ciphers()),
            Str('compression', null=True, enum=['LZO', 'LZ4']),
            Str('device_type', enum=['TUN', 'TAP']),
            Str('protocol', enum=['UDP', 'TCP']),
            Str('remote')
        )
    )
    async def do_update(self, data):
        old_config = await self.config()
        config = old_config.update(data)

        await self.validate(config, 'openvpn_client_update')

        await self._update_service(old_config, config)

        return await self.config()
