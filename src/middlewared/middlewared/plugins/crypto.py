import datetime
import dateutil
import dateutil.parser
import inspect
import ipaddress
import josepy as jose
import json
import os
import random
import re

from middlewared.async_validators import validate_country
from middlewared.schema import accepts, Bool, Dict, Int, List, Patch, Ref, Str
from middlewared.service import CallError, CRUDService, job, periodic, private, Service, skip_arg, ValidationErrors
from middlewared.validators import Email, IpAddress, Range

from acme import client, errors, messages
from OpenSSL import crypto, SSL
from contextlib import suppress

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives.asymmetric import dsa, ec, rsa
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization


CA_TYPE_EXISTING = 0x01
CA_TYPE_INTERNAL = 0x02
CA_TYPE_INTERMEDIATE = 0x04
CERT_TYPE_EXISTING = 0x08
CERT_TYPE_INTERNAL = 0x10
CERT_TYPE_CSR = 0x20

CERT_ROOT_PATH = '/etc/certificates'
CERT_CA_ROOT_PATH = '/etc/certificates/CA'
RE_CERTIFICATE = re.compile(r"(-{5}BEGIN[\s\w]+-{5}[^-]+-{5}END[\s\w]+-{5})+", re.M | re.S)


def get_cert_info_from_data(data):
    cert_info_keys = [
        'key_length', 'country', 'state', 'city', 'organization', 'common', 'key_type', 'ec_curve',
        'san', 'serial', 'email', 'lifetime', 'digest_algorithm', 'organizational_unit'
    ]
    return {key: data.get(key) for key in cert_info_keys if data.get(key)}


async def validate_cert_name(middleware, cert_name, datastore, verrors, name):
    certs = await middleware.call(
        'datastore.query',
        datastore,
        [('cert_name', '=', cert_name)]
    )
    if certs:
        verrors.add(
            name,
            'A certificate with this name already exists'
        )

    if cert_name in ("external", "self-signed", "external - signature pending"):
        verrors.add(
            name,
            f'{cert_name} is a reserved internal keyword for Certificate Management'
        )
    reg = re.search(r'^[a-z0-9_\-]+$', cert_name or '', re.I)
    if not reg:
        verrors.add(
            name,
            'Use alphanumeric characters, "_" and "-".'
        )


def _set_required(name):
    def set_r(attr):
        attr.required = True
    return {'name': name, 'method': set_r}


async def _validate_common_attributes(middleware, data, verrors, schema_name):

    country = data.get('country')
    if country:
        await validate_country(middleware, country, verrors, f'{schema_name}.country')

    certificate = data.get('certificate')
    if certificate:
        matches = RE_CERTIFICATE.findall(certificate)

        if not matches or not await middleware.call('cryptokey.load_certificate', certificate):
            verrors.add(
                f'{schema_name}.certificate',
                'Not a valid certificate'
            )

    private_key = data.get('privatekey')
    passphrase = data.get('passphrase')
    if private_key:
        await middleware.call('cryptokey.validate_private_key', private_key, verrors, schema_name, passphrase)

    signedby = data.get('signedby')
    if signedby:
        valid_signing_ca = await middleware.call(
            'certificateauthority.query',
            [
                ('certificate', '!=', None),
                ('privatekey', '!=', None),
                ('certificate', '!=', ''),
                ('privatekey', '!=', ''),
                ('id', '=', signedby)
            ],
        )

        if not valid_signing_ca:
            verrors.add(
                f'{schema_name}.signedby',
                'Please provide a valid signing authority'
            )

    csr = data.get('CSR')
    if csr:
        if not await middleware.call('cryptokey.load_certificate_request', csr):
            verrors.add(
                f'{schema_name}.CSR',
                'Please provide a valid CSR'
            )

    csr_id = data.get('csr_id')
    if csr_id and not await middleware.call('certificate.query', [['id', '=', csr_id], ['CSR', '!=', None]]):
        verrors.add(
            f'{schema_name}.csr_id',
            'Please provide a valid csr_id which has a valid CSR filed'
        )

    await middleware.call(
        'cryptokey.validate_certificate_with_key', certificate, private_key, schema_name, verrors, passphrase
    )

    key_type = data.get('key_type')
    if key_type:
        if key_type != 'EC' and not data.get('key_length'):
            verrors.add(
                f'{schema_name}.key_length',
                'RSA-based keys require an entry in this field.'
            )


class CryptoKeyService(Service):

    ec_curve_default = 'BrainpoolP384R1'

    ec_curves = [
        'BrainpoolP512R1',
        'BrainpoolP384R1',
        'BrainpoolP256R1',
        'SECP256K1'
    ]

    backend_mappings = {
        'common_name': 'common',
        'country_name': 'country',
        'state_or_province_name': 'state',
        'locality_name': 'city',
        'organization_name': 'organization',
        'organizational_unit_name': 'organizational_unit',
        'email_address': 'email'
    }

    EXTENSIONS = {}

    class Config:
        private = True

    @staticmethod
    def extensions():
        if not CryptoKeyService.EXTENSIONS:
            # For now we only support the following extensions
            supported = [
                'BasicConstraints', 'SubjectKeyIdentifier', 'AuthorityKeyIdentifier',
                'ExtendedKeyUsage', 'KeyUsage', 'SubjectAlternativeName'
            ]

            for attr in filter(
                lambda attr: attr in supported, dir(x509.extensions)
            ):
                attr_obj = getattr(x509.extensions, attr)
                if (
                    inspect.isclass(attr_obj) and issubclass(
                        attr_obj, x509.extensions.ExtensionType
                    ) and x509.extensions.ExtensionType != attr_obj
                ):
                    CryptoKeyService.EXTENSIONS[attr] = inspect.getfullargspec(attr_obj.__init__).args[1:]

        return CryptoKeyService.EXTENSIONS

    def validate_certificate_with_key(self, certificate, private_key, schema_name, verrors, passphrase=None):
        if (
            (certificate and private_key) and
            all(k not in verrors for k in (f'{schema_name}.certificate', f'{schema_name}.privatekey'))
        ):
            public_key_obj = crypto.load_certificate(crypto.FILETYPE_PEM, certificate)
            private_key_obj = crypto.load_privatekey(
                crypto.FILETYPE_PEM,
                private_key,
                passphrase=passphrase.encode() if passphrase else None
            )

            try:
                context = SSL.Context(SSL.TLSv1_2_METHOD)
                context.use_certificate(public_key_obj)
                context.use_privatekey(private_key_obj)
                context.check_privatekey()
            except SSL.Error as e:
                verrors.add(
                    f'{schema_name}.privatekey',
                    f'Private key does not match certificate: {e}'
                )

        return verrors

    def validate_private_key(self, private_key, verrors, schema_name, passphrase=None):
        private_key_obj = self.load_private_key(private_key, passphrase)
        if not private_key_obj:
            verrors.add(
                f'{schema_name}.privatekey',
                'A valid private key is required, with a passphrase if one has been set.'
            )
        elif (
            'create' in schema_name and private_key_obj.key_size < 1024 and not isinstance(
                private_key_obj, ec.EllipticCurvePrivateKey
            )
        ):
            # When a cert/ca is being created, disallow keys with size less then 1024
            # Update is allowed for now for keeping compatibility with very old cert/keys
            # We do not do this check for any EC based key
            verrors.add(
                f'{schema_name}.privatekey',
                'Key size must be greater than or equal to 1024 bits.'
            )

    def parse_cert_date_string(self, date_value):
        t1 = dateutil.parser.parse(date_value)
        t2 = t1.astimezone(dateutil.tz.tzlocal())
        return t2.ctime()

    @accepts(
        Str('certificate', required=True)
    )
    def load_certificate(self, certificate):
        try:
            # digest_algorithm, lifetime, country, state, city, organization, organizational_unit,
            # email, common, san, serial, chain, fingerprint
            cert = crypto.load_certificate(
                crypto.FILETYPE_PEM,
                certificate
            )
        except crypto.Error:
            return {}
        else:
            cert_info = self.get_x509_subject(cert)

            valid_algos = ('SHA1', 'SHA224', 'SHA256', 'SHA384', 'SHA512')
            signature_algorithm = cert.get_signature_algorithm().decode()
            # Certs signed with RSA keys will have something like
            # sha256WithRSAEncryption
            # Certs signed with EC keys will have something like
            # ecdsa-with-SHA256
            m = re.match('^(.+)[Ww]ith', signature_algorithm)
            if m:
                cert_info['digest_algorithm'] = m.group(1).upper()

            if cert_info.get('digest_algorithm') not in valid_algos:
                cert_info['digest_algorithm'] = (signature_algorithm or '').split('-')[-1].strip()

            if cert_info['digest_algorithm'] not in valid_algos:
                # Let's log this please
                self.logger.debug(f'Failed to parse signature algorithm {signature_algorithm} for {certificate}')

            cert_info.update({
                'lifetime': (
                    dateutil.parser.parse(cert.get_notAfter()) - dateutil.parser.parse(cert.get_notBefore())
                ).days,
                'from': self.parse_cert_date_string(cert.get_notBefore()),
                'until': self.parse_cert_date_string(cert.get_notAfter()),
                'serial': cert.get_serial_number(),
                'chain': len(RE_CERTIFICATE.findall(certificate)) > 1,
                'fingerprint': cert.digest('sha1').decode()
            })

            return cert_info

    def get_x509_subject(self, obj):
        cert_info = {
            'country': obj.get_subject().C,
            'state': obj.get_subject().ST,
            'city': obj.get_subject().L,
            'organization': obj.get_subject().O,
            'organizational_unit': obj.get_subject().OU,
            'common': obj.get_subject().CN,
            'san': [],
            'email': obj.get_subject().emailAddress,
            'DN': '',
        }

        for ext in (
            map(
                lambda i: obj.get_extension(i),
                range(obj.get_extension_count())
            ) if isinstance(obj, crypto.X509) else obj.get_extensions()
        ):
            if 'subjectAltName' == ext.get_short_name().decode():
                cert_info['san'] = [s.strip() for s in ext.__str__().split(',') if s]

        dn = []
        for k, v in obj.get_subject().get_components():
            if k.decode() == 'subjectAltName':
                continue

            dn.append(f'{k.decode()}={v.decode()}')

        cert_info['DN'] = f'/{"/".join(dn)}'

        if cert_info['san']:
            # We should always trust the extension instead of the subject for SAN
            cert_info['DN'] += f'/subjectAltName={", ".join(cert_info["san"])}'

        return cert_info

    @accepts(
        Str('csr', required=True)
    )
    def load_certificate_request(self, csr):
        try:
            csr_obj = crypto.load_certificate_request(crypto.FILETYPE_PEM, csr)
        except crypto.Error:
            return {}
        else:
            return self.get_x509_subject(csr_obj)

    def generate_self_signed_certificate(self):
        cert = self.generate_builder({
            'crypto_subject_name': {
                'country_name': 'US',
                'organization_name': 'iXsystems',
                'common_name': 'localhost',
                'email_address': 'info@ixsystems.com'
            },
            'lifetime': 3600
        })
        key = self.generate_private_key({
            'serialize': False,
            'key_length': 2048,
            'type': 'RSA'
        })

        cert = cert.public_key(
            key.public_key()
        ).sign(
            key, hashes.SHA256(), default_backend()
        )

        return (
            cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ).decode()
        )

    def normalize_san(self, san_list):
        # TODO: ADD MORE TYPES WRT RFC'S
        normalized = []
        ip_validator = IpAddress()
        for count, san in enumerate(san_list or []):
            try:
                ip_validator(san)
            except ValueError:
                normalized.append(['DNS', san])
            else:
                normalized.append(['IP', san])

        return normalized

    @accepts(
        Patch(
            'certificate_cert_info', 'generate_certificate_signing_request',
            ('rm', {'name': 'lifetime'})
        )
    )
    def generate_certificate_signing_request(self, data):
        key = self.generate_private_key({
            'type': data.get('key_type') or 'RSA',
            'curve': data.get('ec_curve') or self.ec_curve_default,
            'key_length': data.get('key_length') or 2048
        })

        csr = self.generate_builder({
            'crypto_subject_name': {
                k: data.get(v) for k, v in self.backend_mappings.items()
            },
            'san': self.normalize_san(data.get('san') or []),
            'serial': data.get('serial'),
            'lifetime': data.get('lifetime'),
            'csr': True
        })

        csr = csr.sign(key, getattr(hashes, data.get('digest_algorithm') or 'SHA256')(), default_backend())

        return (
            csr.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ).decode()
        )

    @accepts(
        Dict(
            'certificate_cert_info',
            Int('key_length'),
            Int('serial', required=False, null=True),
            Int('lifetime', required=True),
            Str('ca_certificate', required=False),
            Str('ca_privatekey', required=False),
            Str('key_type', required=False),
            Str('ec_curve', required=False),
            Str('country', required=True),
            Str('state', required=True),
            Str('city', required=True),
            Str('organization', required=True),
            Str('organizational_unit'),
            Str('common', required=True),
            Str('email', validators=[Email()], required=True),
            Str('digest_algorithm', enum=['SHA1', 'SHA224', 'SHA256', 'SHA384', 'SHA512']),
            List('san', items=[Str('san')], null=True),
            register=True
        )
    )
    def generate_certificate(self, data):
        key = self.generate_private_key({
            'type': data.get('key_type') or 'RSA',
            'curve': data.get('ec_curve') or self.ec_curve_default,
            'key_length': data.get('key_length') or 2048
        })

        if data.get('ca_privatekey'):
            ca_key = self.load_private_key(data['ca_privatekey'])
        else:
            ca_key = None

        san_list = self.normalize_san(data.get('san'))

        builder_data = {
            'crypto_subject_name': {
                k: data.get(v) for k, v in self.backend_mappings.items()
            },
            'san': san_list,
            'serial': data.get('serial'),
            'lifetime': data.get('lifetime')
        }
        if data.get('ca_certificate'):
            ca_data = self.load_certificate(data['ca_certificate'])
            builder_data['crypto_issuer_name'] = {
                k: ca_data.get(v) for k, v in self.backend_mappings.items()
            }

        cert = self.generate_builder(builder_data)

        cert = cert.public_key(
            key.public_key()
        ).add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False
        ).sign(
            ca_key or key, getattr(hashes, data.get('digest_algorithm') or 'SHA256')(), default_backend()
        )

        return (
            cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ).decode()
        )

    @accepts(
        Ref('certificate_cert_info')
    )
    def generate_self_signed_ca(self, data):
        return self.generate_certificate_authority(data)

    @accepts(
        Ref('certificate_cert_info')
    )
    def generate_certificate_authority(self, data):
        key = self.generate_private_key({
            'type': data.get('key_type') or 'RSA',
            'curve': data.get('ec_curve') or self.ec_curve_default,
            'key_length': data.get('key_length') or 2048
        })

        if data.get('ca_privatekey'):
            ca_key = self.load_private_key(data['ca_privatekey'])
        else:
            ca_key = None

        san_list = self.normalize_san(data.get('san') or [])

        builder_data = {
            'crypto_subject_name': {
                k: data.get(v) for k, v in self.backend_mappings.items()
            },
            'san': san_list,
            'serial': data.get('serial'),
            'lifetime': data.get('lifetime')
        }
        if data.get('ca_certificate'):
            ca_data = self.load_certificate(data['ca_certificate'])
            builder_data['crypto_issuer_name'] = {
                k: ca_data.get(v) for k, v in self.backend_mappings.items()
            }

        cert = self.generate_builder(builder_data)

        cert = cert.add_extension(
            x509.BasicConstraints(True, 0 if ca_key else None), True
        ).add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False, key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False
            ), True
        ).add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False
        ).public_key(
            key.public_key()
        )

        cert = cert.sign(ca_key or key, getattr(hashes, data.get('digest_algorithm') or 'SHA256')(), default_backend())

        return (
            cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ).decode()
        )

    @accepts(
        Dict(
            'sign_csr',
            Str('ca_certificate', required=True),
            Str('ca_privatekey', required=True),
            Str('csr', required=True),
            Str('csr_privatekey', required=True),
            Int('serial', required=True),
            Str('digest_algorithm', default='SHA256')
        )
    )
    def sign_csr_with_ca(self, data):
        csr_data = self.load_certificate_request(data['csr'])
        ca_data = self.load_certificate(data['ca_certificate'])
        ca_key = self.load_private_key(data['ca_privatekey'])
        csr_key = self.load_private_key(data['csr_privatekey'])
        new_cert = self.generate_builder({
            'crypto_subject_name': {
                k: csr_data.get(v) for k, v in self.backend_mappings.items()
            },
            'crypto_issuer_name': {
                k: ca_data.get(v) for k, v in self.backend_mappings.items()
            },
            'serial': data['serial'],
            'san': self.normalize_san(csr_data.get('san'))
        })

        new_cert = new_cert.public_key(
            csr_key.public_key()
        ).sign(
            ca_key, getattr(hashes, data.get('digest_algorithm') or 'SHA256')(), default_backend()
        )

        return new_cert.public_bytes(serialization.Encoding.PEM).decode()

    def generate_builder(self, options):
        # We expect backend_mapping keys for crypto_subject_name attr in options and for crypto_issuer_name as well
        data = {}
        for key in ('crypto_subject_name', 'crypto_issuer_name'):
            data[key] = x509.Name([
                x509.NameAttribute(getattr(NameOID, k.upper()), v)
                for k, v in (options.get(key) or {}).items() if v
            ])
        if not data['crypto_issuer_name']:
            data['crypto_issuer_name'] = data['crypto_subject_name']

        # Lifetime represents no of days
        # Let's normalize lifetime value
        not_valid_before = datetime.datetime.utcnow()
        not_valid_after = datetime.datetime.utcnow() + datetime.timedelta(days=options.get('lifetime') or 3600)

        # Let's normalize `san`
        san = x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address(v)) if t == 'IP' else x509.DNSName(v)
            for t, v in options.get('san') or []
        ])

        builder = x509.CertificateSigningRequestBuilder if options.get('csr') else x509.CertificateBuilder

        cert = builder(
            subject_name=data['crypto_subject_name']
        )

        if not options.get('csr'):
            cert = cert.issuer_name(
                data['crypto_issuer_name']
            ).not_valid_before(
                not_valid_before
            ).not_valid_after(
                not_valid_after
            ).serial_number(options.get('serial') or 1)

        if san:
            cert = cert.add_extension(san, False)

        return cert

    @accepts(
        Dict(
            'generate_private_key',
            Bool('serialize', default=False),
            Int('key_length', default=2048),
            Str('type', default='RSA', enum=['RSA', 'EC']),
            Str('curve', enum=ec_curves, default='BrainpoolP384R1')
        )
    )
    def generate_private_key(self, options):
        # We should make sure to return in PEM format
        # Reason for using PKCS8
        # https://stackoverflow.com/questions/48958304/pkcs1-and-pkcs8-format-for-rsa-private-key

        if options.get('type') == 'EC':
            key = ec.generate_private_key(
                getattr(ec, options.get('curve')),
                default_backend()
            )
        else:
            key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=options.get('key_length'),
                backend=default_backend()
            )

        if options.get('serialize'):
            return key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ).decode()
        else:
            return key

    def load_private_key(self, key_string, passphrase=None):
        with suppress(ValueError, TypeError, AttributeError):
            return serialization.load_pem_private_key(
                key_string.encode(),
                password=passphrase.encode() if passphrase else None,
                backend=default_backend()
            )

    def export_private_key(self, buffer, passphrase=None):
        key = self.load_private_key(buffer, passphrase)
        if key:
            return key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ).decode()


class CertificateService(CRUDService):

    class Config:
        datastore = 'system.certificate'
        datastore_extend = 'certificate.cert_extend'
        datastore_prefix = 'cert_'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.map_functions = {
            'CERTIFICATE_CREATE_INTERNAL': self.__create_internal,
            'CERTIFICATE_CREATE_IMPORTED': self.__create_imported_certificate,
            'CERTIFICATE_CREATE_IMPORTED_CSR': self.__create_imported_csr,
            'CERTIFICATE_CREATE_CSR': self.__create_csr,
            'CERTIFICATE_CREATE_ACME': self.__create_acme_certificate,
        }

    @private
    async def cert_extend(self, cert):
        """Extend certificate with some useful attributes."""

        if cert.get('signedby'):

            # We query for signedby again to make sure it's keys do not have the "cert_" prefix and it has gone through
            # the cert_extend method
            # Datastore query is used instead of certificate.query to stop an infinite recursive loop

            cert['signedby'] = await self.middleware.call(
                'datastore.query',
                'system.certificateauthority',
                [('id', '=', cert['signedby']['id'])],
                {
                    'prefix': 'cert_',
                    'extend': 'certificate.cert_extend',
                    'get': True
                }
            )

        # Remove ACME related keys if cert is not an ACME based cert
        if not cert.get('acme'):
            for key in ['acme', 'acme_uri', 'domains_authenticators', 'renew_days']:
                cert.pop(key, None)

        if cert['type'] in (
                CA_TYPE_EXISTING, CA_TYPE_INTERNAL, CA_TYPE_INTERMEDIATE
        ):
            root_path = CERT_CA_ROOT_PATH
        else:
            root_path = CERT_ROOT_PATH
        cert['root_path'] = root_path
        cert['certificate_path'] = os.path.join(
            root_path, f'{cert["name"]}.crt'
        )
        cert['privatekey_path'] = os.path.join(
            root_path, f'{cert["name"]}.key'
        )
        cert['csr_path'] = os.path.join(
            root_path, f'{cert["name"]}.csr'
        )

        cert['cert_type'] = 'CA' if root_path == CERT_CA_ROOT_PATH else 'CERTIFICATE'

        if cert['cert_type'] == 'CA':
            # TODO: Should we look for intermediate ca's as well which this ca has signed ?
            cert['signed_certificates'] = len((
                await self.middleware.call(
                    'datastore.query',
                    'system.certificate',
                    [['signedby', '=', cert['id']]],
                    {'prefix': 'cert_'}
                )
            ))

        if not os.path.exists(root_path):
            os.makedirs(root_path, 0o755, exist_ok=True)

        def cert_issuer(cert):
            issuer = None
            if cert['type'] in (CA_TYPE_EXISTING, CERT_TYPE_EXISTING):
                issuer = "external"
            elif cert['type'] == CA_TYPE_INTERNAL:
                issuer = "self-signed"
            elif cert['type'] in (CERT_TYPE_INTERNAL, CA_TYPE_INTERMEDIATE):
                issuer = cert['signedby']
            elif cert['type'] == CERT_TYPE_CSR:
                issuer = "external - signature pending"
            return issuer

        cert['issuer'] = cert_issuer(cert)

        cert['chain_list'] = []
        certs = []
        if len(RE_CERTIFICATE.findall(cert['certificate'] or '')) > 1:
            certs = RE_CERTIFICATE.findall(cert['certificate'])
        elif cert['type'] != CERT_TYPE_CSR:
            certs = [cert['certificate']]
            signing_CA = cert['issuer']
            # Recursively get all internal/intermediate certificates
            # FIXME: NONE HAS BEEN ADDED IN THE FOLLOWING CHECK FOR CSR'S WHICH HAVE BEEN SIGNED BY A CA
            while signing_CA not in ["external", "self-signed", "external - signature pending", None]:
                certs.append(signing_CA['certificate'])
                signing_CA['issuer'] = cert_issuer(signing_CA)
                signing_CA = signing_CA['issuer']

        failed_parsing = False
        for c in certs:
            if c and await self.middleware.call('cryptokey.load_certificate', c):
                cert['chain_list'].append(c)
            else:
                self.logger.debug(f'Failed to load certificate chain of {cert["name"]}', exc_info=True)
                break

        if certs:
            # This indicates cert is not CSR and a cert
            cert_data = await self.middleware.call('cryptokey.load_certificate', cert['certificate'])
            cert.update(cert_data)
            if not cert_data:
                self.logger.error(f'Failed to load certificate {cert["name"]}')
                failed_parsing = True

        if cert['privatekey']:
            key_obj = await self.middleware.call('cryptokey.load_private_key', cert['privatekey'])
            if key_obj:
                cert['key_length'] = key_obj.key_size
                if isinstance(key_obj, ec.EllipticCurvePrivateKey):
                    cert['key_type'] = 'EC'
                elif isinstance(key_obj, rsa.RSAPrivateKey):
                    cert['key_type'] = 'RSA'
                elif isinstance(key_obj, dsa.DSAPrivateKey):
                    cert['key_type'] = 'DSA'
                else:
                    cert['key_type'] = 'OTHER'
            else:
                self.logger.debug(f'Failed to load privatekey of {cert["name"]}', exc_info=True)
                cert['key_length'] = cert['key_type'] = None

        if cert['type'] == CERT_TYPE_CSR:
            csr_data = await self.middleware.call('cryptokey.load_certificate_request', cert['CSR'])
            if csr_data:
                cert.update(csr_data)

                cert.update({k: None for k in ('from', 'until')})  # CSR's don't have from, until - normalizing keys
            else:
                self.logger.debug(f'Failed to load csr {cert["name"]}', exc_info=True)
                failed_parsing = True

        if failed_parsing:
            # Normalizing cert/csr
            # Should we perhaps set the value to something like "MALFORMED_CERTIFICATE" for this list off attrs ?
            cert.update({
                key: None for key in [
                    'digest_algorithm', 'lifetime', 'country', 'state', 'city', 'from', 'until',
                    'organization', 'organizational_unit', 'email', 'common', 'san', 'serial', 'fingerprint'
                ]
            })

        cert['parsed'] = not failed_parsing

        cert['internal'] = 'NO' if cert['type'] in (CA_TYPE_EXISTING, CERT_TYPE_EXISTING) else 'YES'
        cert['CA_type_existing'] = bool(cert['type'] & CA_TYPE_EXISTING)
        cert['CA_type_internal'] = bool(cert['type'] & CA_TYPE_INTERNAL)
        cert['CA_type_intermediate'] = bool(cert['type'] & CA_TYPE_INTERMEDIATE)
        cert['cert_type_existing'] = bool(cert['type'] & CERT_TYPE_EXISTING)
        cert['cert_type_internal'] = bool(cert['type'] & CERT_TYPE_INTERNAL)
        cert['cert_type_CSR'] = bool(cert['type'] & CERT_TYPE_CSR)

        return cert

    # HELPER METHODS

    @private
    async def cert_services_validation(self, id, schema_name, raise_verrors=True):
        # General method to check certificate health wrt usage in services
        cert = await self.middleware.call('certificate.query', [['id', '=', id]])
        verrors = ValidationErrors()
        if cert:
            cert = cert[0]
            if cert['cert_type'] != 'CERTIFICATE' or cert['cert_type_CSR']:
                verrors.add(
                    schema_name,
                    'Selected certificate id is not a valid certificate'
                )
            elif not cert.get('fingerprint'):
                verrors.add(
                    schema_name,
                    f'{cert["name"]} certificate is malformed'
                )

            if not cert['key_length']:
                verrors.add(
                    schema_name,
                    'Failed to parse certificate\'s private key'
                )
            elif cert['key_type'] != 'EC' and cert['key_length'] < 1024:
                verrors.add(
                    schema_name,
                    f'{cert["name"]}\'s private key size is less then 1024 bits'
                )
        else:
            verrors.add(
                schema_name,
                f'No Certificate found with the provided id: {id}'
            )

        if raise_verrors:
            verrors.check()
        else:
            return verrors

    @private
    async def validate_common_attributes(self, data, schema_name):
        verrors = ValidationErrors()

        await _validate_common_attributes(self.middleware, data, verrors, schema_name)

        return verrors

    @private
    async def get_domain_names(self, cert_id):
        data = await self._get_instance(int(cert_id))
        names = [data['common']]
        names.extend(data['san'])
        return names

    @private
    def get_acme_client_and_key(self, acme_directory_uri, tos=False):
        data = self.middleware.call_sync('acme.registration.query', [['directory', '=', acme_directory_uri]])
        if not data:
            data = self.middleware.call_sync(
                'acme.registration.create',
                {'tos': tos, 'acme_directory_uri': acme_directory_uri}
            )
        else:
            data = data[0]
        # Making key now
        key = jose.JWKRSA.fields_from_json(json.loads(data['body']['key']))
        key_dict = key.fields_to_partial_json()
        # Making registration resource now
        registration = messages.RegistrationResource.from_json({
            'uri': data['uri'],
            'terms_of_service': data['tos'],
            'body': {
                'contact': [data['body']['contact']],
                'status': data['body']['status'],
                'key': {
                    'e': key_dict['e'],
                    'kty': 'RSA',
                    'n': key_dict['n']
                }
            }
        })

        return client.ClientV2(
            messages.Directory({
                'newAccount': data['new_account_uri'],
                'newNonce': data['new_nonce_uri'],
                'newOrder': data['new_order_uri'],
                'revokeCert': data['revoke_cert_uri']
            }),
            client.ClientNetwork(key, account=registration)
        ), key

    @private
    def acme_issue_certificate(self, job, progress, data, csr_data):
        verrors = ValidationErrors()

        # TODO: Add ability to complete DNS validation challenge manually

        # Validate domain dns mapping for handling DNS challenges
        # Ensure that there is an authenticator for each domain in the CSR
        domains = self.middleware.call_sync('certificate.get_domain_names', csr_data['id'])
        dns_authenticator_ids = [o['id'] for o in self.middleware.call_sync('acme.dns.authenticator.query')]
        for domain in domains:
            if domain not in data['dns_mapping']:
                verrors.add(
                    'acme_create.dns_mapping',
                    f'Please provide DNS authenticator id for {domain}'
                )
            elif data['dns_mapping'][domain] not in dns_authenticator_ids:
                verrors.add(
                    'acme_create.dns_mapping',
                    f'Provided DNS Authenticator id for {domain} does not exist'
                )
            if domain.endswith('.'):
                verrors.add(
                    'acme_create.dns_mapping',
                    f'Domain {domain} name cannot end with a period'
                )
            if '*' in domain and not domain.startswith('*.'):
                verrors.add(
                    'acme_create.dns_mapping',
                    'Wildcards must be at the start of domain name followed by a period'
                )
        for domain in data['dns_mapping']:
            if domain not in domains:
                verrors.add(
                    'acme_create.dns_mapping',
                    f'{domain} not specified in the CSR'
                )

        if verrors:
            raise verrors

        acme_client, key = self.get_acme_client_and_key(data['acme_directory_uri'], data['tos'])
        try:
            # perform operations and have a cert issued
            order = acme_client.new_order(csr_data['CSR'])
        except messages.Error as e:
            raise CallError(f'Failed to issue a new order for Certificate : {e}')
        else:
            job.set_progress(progress, 'New order for certificate issuance placed')

            self.handle_authorizations(job, progress, order, data['dns_mapping'], acme_client, key)

            try:
                # Polling for a maximum of 10 minutes while trying to finalize order
                # Should we try .poll() instead first ? research please
                return acme_client.poll_and_finalize(order, datetime.datetime.now() + datetime.timedelta(minutes=10))
            except errors.TimeoutError:
                raise CallError('Certificate request for final order timed out')

    @private
    def handle_authorizations(self, job, progress, order, domain_names_dns_mapping, acme_client, key):
        # When this is called, it should be ensured by the function calling this function that for all authorization
        # resource, a domain name dns mapping is available
        # For multiple domain providers in domain names, I think we should ask the end user to specify which domain
        # provider is used for which domain so authorizations can be handled gracefully

        max_progress = (progress * 4) - progress - (progress * 4 / 5)

        dns_mapping = {d.replace('*.', ''): v for d, v in domain_names_dns_mapping.items()}
        for authorization_resource in order.authorizations:
            try:
                status = False
                progress += (max_progress / len(order.authorizations))
                domain = authorization_resource.body.identifier.value
                # BOULDER DOES NOT RETURN WILDCARDS FOR NOW
                # OTHER IMPLEMENTATIONS RIGHT NOW ASSUME THAT EVERY DOMAIN HAS A WILD CARD IN CASE OF DNS CHALLENGE
                challenge = None
                for chg in authorization_resource.body.challenges:
                    if chg.typ == 'dns-01':
                        challenge = chg

                if not challenge:
                    raise CallError(
                        f'DNS Challenge not found for domain {authorization_resource.body.identifier.value}'
                    )

                self.middleware.call_sync(
                    'acme.dns.authenticator.update_txt_record', {
                        'authenticator': dns_mapping[domain],
                        'challenge': challenge.json_dumps(),
                        'domain': domain,
                        'key': key.json_dumps()
                    }
                )

                try:
                    status = acme_client.answer_challenge(challenge, challenge.response(key))
                except errors.UnexpectedUpdate as e:
                    raise CallError(
                        f'Error answering challenge for {domain} : {e}'
                    )
            finally:
                job.set_progress(
                    progress,
                    f'DNS challenge {"completed" if status else "failed"} for {domain}'
                )

    @periodic(86400, run_on_start=True)
    @private
    @job(lock='acme_cert_renewal')
    def renew_certs(self, job):
        certs = self.middleware.call_sync(
            'certificate.query',
            [['acme', '!=', None]]
        )

        progress = 0
        for cert in certs:
            progress += (100 / len(certs))

            if (
                datetime.datetime.strptime(cert['until'], '%a %b %d %H:%M:%S %Y') - datetime.datetime.utcnow()
            ).days < cert['renew_days']:
                # renew cert
                self.logger.debug(f'Renewing certificate {cert["name"]}')
                final_order = self.acme_issue_certificate(
                    job, progress / 4, {
                        'tos': True,
                        'acme_directory_uri': cert['acme']['directory'],
                        'dns_mapping': cert['domains_authenticators']
                    },
                    cert
                )

                self.middleware.call_sync(
                    'datastore.update',
                    self._config.datastore,
                    cert['id'],
                    {
                        'certificate': final_order.fullchain_pem,
                        'acme_uri': final_order.uri
                    },
                    {'prefix': self._config.datastore_prefix}
                )

            job.set_progress(progress)

    @accepts()
    async def acme_server_choices(self):
        """
        Dictionary of popular ACME Servers with their directory URI endpoints which we display automatically
        in UI
        """
        return {
            'https://acme-staging-v02.api.letsencrypt.org/directory': 'Let\'s Encrypt Staging Directory',
            'https://acme-v02.api.letsencrypt.org/directory': 'Let\'s Encrypt Production Directory'
        }

    @accepts()
    async def ec_curve_choices(self):
        """
        Dictionary of supported EC curves.
        """
        return {k: k for k in CryptoKeyService.ec_curves}

    @accepts()
    async def key_type_choices(self):
        """
        Dictionary of supported key types for certificates.
        """
        return {k: k for k in ['RSA', 'EC']}

    @accepts()
    async def certificate_extensions(self):
        return CryptoKeyService.extensions()

    # CREATE METHODS FOR CREATING CERTIFICATES
    # "do_create" IS CALLED FIRST AND THEN BASED ON THE TYPE OF THE CERTIFICATE WHICH IS TO BE CREATED THE
    # APPROPRIATE METHOD IS CALLED
    # FOLLOWING TYPES ARE SUPPORTED
    # CREATE_TYPE ( STRING )          - METHOD CALLED
    # CERTIFICATE_CREATE_INTERNAL     - __create_internal
    # CERTIFICATE_CREATE_IMPORTED     - __create_imported_certificate
    # CERTIFICATE_CREATE_IMPORTED_CSR - __create_imported_csr
    # CERTIFICATE_CREATE_CSR          - __create_csr
    # CERTIFICATE_CREATE_ACME         - __create_acme_certificate

    @accepts(
        Dict(
            'certificate_create',
            Bool('tos'),
            Dict('dns_mapping', additional_attrs=True),
            Int('csr_id'),
            Int('signedby'),
            Int('key_length', enum=[1024, 2048, 4096]),
            Int('renew_days'),
            Int('type'),
            Int('lifetime'),
            Int('serial', validators=[Range(min=1)]),
            Str('acme_directory_uri'),
            Str('certificate'),
            Str('city'),
            Str('common'),
            Str('country'),
            Str('CSR'),
            Str('ec_curve', enum=CryptoKeyService.ec_curves, default=CryptoKeyService.ec_curve_default),
            Str('email', validators=[Email()]),
            Str('key_type', enum=['RSA', 'EC'], default='RSA'),
            Str('name', required=True),
            Str('organization'),
            Str('organizational_unit'),
            Str('passphrase'),
            Str('privatekey'),
            Str('state'),
            Str('create_type', enum=[
                'CERTIFICATE_CREATE_INTERNAL', 'CERTIFICATE_CREATE_IMPORTED',
                'CERTIFICATE_CREATE_CSR', 'CERTIFICATE_CREATE_IMPORTED_CSR',
                'CERTIFICATE_CREATE_ACME'], required=True),
            Str('digest_algorithm', enum=['SHA1', 'SHA224', 'SHA256', 'SHA384', 'SHA512']),
            List('san', items=[Str('san')]),
            register=True
        )
    )
    @job(lock='cert_create')
    async def do_create(self, job, data):
        """
        Create a new Certificate

        Certificates are classified under following types and the necessary keywords to be passed
        for `create_type` attribute to create the respective type of certificate

        1) Internal Certificate                 -  CERTIFICATE_CREATE_INTERNAL

        2) Imported Certificate                 -  CERTIFICATE_CREATE_IMPORTED

        3) Certificate Signing Request          -  CERTIFICATE_CREATE_CSR

        4) Imported Certificate Signing Request -  CERTIFICATE_CREATE_IMPORTED_CSR

        5) ACME Certificate                     -  CERTIFICATE_CREATE_ACME

        By default, created certs use RSA keys. If an Elliptic Curve Key is desired, it can be specified with the
        `key_type` attribute. If the `ec_curve` attribute is not specified for the Elliptic Curve Key, then default to
        using "BrainpoolP384R1" curve.

        A type is selected by the Certificate Service based on `create_type`. The rest of the values in `data` are
        validated accordingly and finally a certificate is made based on the selected type.

        .. examples(websocket)::

          Create an ACME based certificate

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "certificate.create",
                "params": [{
                    "tos": true,
                    "csr_id": 1,
                    "acme_directory_uri": "https://acme-staging-v02.api.letsencrypt.org/directory",
                    "name": "acme_certificate",
                    "dns_mapping": {
                        "domain1.com": "1"
                    },
                    "create_type": "CERTIFICATE_CREATE_ACME"
                }]
            }

          Create an Imported Certificate Signing Request

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "certificate.create",
                "params": [{
                    "name": "csr",
                    "CSR": "CSR string",
                    "privatekey": "Private key string",
                    "create_type": "CERTIFICATE_CREATE_IMPORTED_CSR"
                }]
            }

          Create an Internal Certificate

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "certificate.create",
                "params": [{
                    "name": "internal_cert",
                    "key_length": 2048,
                    "lifetime": 3600,
                    "city": "Nashville",
                    "common": "domain1.com",
                    "country": "US",
                    "email": "dev@ixsystems.com",
                    "organization": "iXsystems",
                    "state": "Tennessee",
                    "digest_algorithm": "SHA256",
                    "signedby": 4,
                    "create_type": "CERTIFICATE_CREATE_INTERNAL"
                }]
            }
        """
        if not data.get('dns_mapping'):
            data.pop('dns_mapping')  # Default dict added

        create_type = data.pop('create_type')
        if create_type in (
            'CERTIFICATE_CREATE_IMPORTED_CSR', 'CERTIFICATE_CREATE_ACME', 'CERTIFICATE_CREATE_IMPORTED'
        ):
            for key in ('key_length', 'key_type', 'ec_curve'):
                data.pop(key, None)

        verrors = await self.validate_common_attributes(data, 'certificate_create')

        await validate_cert_name(
            self.middleware, data['name'], self._config.datastore,
            verrors, 'certificate_create.name'
        )

        if verrors:
            raise verrors

        job.set_progress(10, 'Initial validation complete')

        if create_type == 'CERTIFICATE_CREATE_ACME':
            data = await self.middleware.run_in_thread(
                self.map_functions[create_type],
                job, data
            )
        else:
            data = await self.map_functions[create_type](job, data)

        data = {
            k: v for k, v in data.items()
            if k in [
                'name', 'certificate', 'CSR', 'privatekey', 'type', 'signedby', 'acme', 'acme_uri',
                'domains_authenticators', 'renew_days'
            ]
        }

        pk = await self.middleware.call(
            'datastore.insert',
            self._config.datastore,
            data,
            {'prefix': self._config.datastore_prefix}
        )

        await self.middleware.call('service.start', 'ssl')

        job.set_progress(100, 'Certificate created successfully')

        return await self._get_instance(pk)

    @accepts(
        Dict(
            'acme_create',
            Bool('tos', default=False),
            Int('csr_id', required=True),
            Int('renew_days', default=10, validators=[Range(min=1)]),
            Str('acme_directory_uri', required=True),
            Str('name', required=True),
            Dict('dns_mapping', additional_attrs=True, required=True)
        )
    )
    @skip_arg(count=1)
    def __create_acme_certificate(self, job, data):

        csr_data = self.middleware.call_sync(
            'certificate._get_instance', data['csr_id']
        )

        data['acme_directory_uri'] += '/' if data['acme_directory_uri'][-1] != '/' else ''

        final_order = self.acme_issue_certificate(job, 25, data, csr_data)

        job.set_progress(95, 'Final order received from ACME server')

        cert_dict = {
            'acme': self.middleware.call_sync(
                'acme.registration.query',
                [['directory', '=', data['acme_directory_uri']]]
            )[0]['id'],
            'acme_uri': final_order.uri,
            'certificate': final_order.fullchain_pem,
            'CSR': csr_data['CSR'],
            'privatekey': csr_data['privatekey'],
            'name': data['name'],
            'type': CERT_TYPE_EXISTING,
            'domains_authenticators': data['dns_mapping'],
            'renew_days': data['renew_days']
        }

        return cert_dict

    @accepts(
        Patch(
            'certificate_create_internal', 'certificate_create_csr',
            ('rm', {'name': 'signedby'}),
            ('rm', {'name': 'lifetime'})
        )
    )
    @skip_arg(count=1)
    async def __create_csr(self, job, data):
        # no signedby, lifetime attributes required
        cert_info = get_cert_info_from_data(data)

        data['type'] = CERT_TYPE_CSR

        req, key = await self.middleware.call(
            'cryptokey.generate_certificate_signing_request',
            cert_info
        )

        job.set_progress(80)

        data['CSR'] = req
        data['privatekey'] = key

        job.set_progress(90, 'Finalizing changes')

        return data

    @accepts(
        Dict(
            'create_imported_csr',
            Str('CSR', required=True),
            Str('name'),
            Str('privatekey', required=True),
            Str('passphrase')
        )
    )
    @skip_arg(count=1)
    async def __create_imported_csr(self, job, data):

        # TODO: We should validate csr with private key ?

        data['type'] = CERT_TYPE_CSR

        job.set_progress(80)

        if 'passphrase' in data:
            data['privatekey'] = await self.middleware.call(
                'cryptokey.export_private_key',
                data['privatekey'],
                data['passphrase']
            )

        job.set_progress(90, 'Finalizing changes')

        return data

    @accepts(
        Dict(
            'certificate_create_imported',
            Int('csr_id'),
            Str('certificate', required=True),
            Str('name'),
            Str('passphrase'),
            Str('privatekey')
        )
    )
    @skip_arg(count=1)
    async def __create_imported_certificate(self, job, data):
        verrors = ValidationErrors()

        csr_id = data.pop('csr_id', None)
        if csr_id:
            csr_obj = await self.query(
                [
                    ['id', '=', csr_id],
                    ['type', '=', CERT_TYPE_CSR]
                ],
                {'get': True}
            )

            data['privatekey'] = csr_obj['privatekey']
            data.pop('passphrase', None)
        elif not data.get('privatekey'):
            verrors.add(
                'certificate_create.privatekey',
                'Private key is required when importing a certificate'
            )

        if verrors:
            raise verrors

        job.set_progress(50, 'Validation complete')

        data['type'] = CERT_TYPE_EXISTING

        if 'passphrase' in data:
            data['privatekey'] = await self.middleware.call(
                'cryptokey.export_private_key',
                data['privatekey'],
                data['passphrase']
            )

        return data

    @accepts(
        Patch(
            'certificate_create', 'certificate_create_internal',
            ('edit', _set_required('digest_algorithm')),
            ('edit', _set_required('lifetime')),
            ('edit', _set_required('country')),
            ('edit', _set_required('state')),
            ('edit', _set_required('city')),
            ('edit', _set_required('organization')),
            ('edit', _set_required('email')),
            ('edit', _set_required('common')),
            ('edit', _set_required('signedby')),
            ('rm', {'name': 'create_type'}),
            register=True
        )
    )
    @skip_arg(count=1)
    async def __create_internal(self, job, data):

        cert_info = get_cert_info_from_data(data)
        data['type'] = CERT_TYPE_INTERNAL

        signing_cert = await self.middleware.call(
            'certificateauthority.query',
            [('id', '=', data['signedby'])],
            {'get': True}
        )

        cert_serial = await self.middleware.call(
            'certificateauthority.get_serial_for_certificate',
            data['signedby']
        )

        cert_info.update({
            'ca_privatekey': signing_cert['privatekey'],
            'ca_certificate': signing_cert['certificate'],
            'serial': cert_serial
        })

        cert, key = await self.middleware.call(
            'cryptokey.generate_certificate',
            cert_info
        )

        data['certificate'] = cert
        data['privatekey'] = key

        job.set_progress(90, 'Finalizing changes')

        return data

    @accepts(
        Int('id', required=True),
        Dict(
            'certificate_update',
            Bool('revoked'),
            Str('name')
        )
    )
    @job(lock='cert_update')
    async def do_update(self, job, id, data):
        """
        Update certificate of `id`

        Only name attribute can be updated

        .. examples(websocket)::

          Update a certificate of `id`

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "certificate.update",
                "params": [
                    1,
                    {
                        "name": "updated_name"
                    }
                ]
            }
        """
        old = await self._get_instance(id)
        # signedby is changed back to integer from a dict
        old['signedby'] = old['signedby']['id'] if old.get('signedby') else None
        if old.get('acme'):
            old['acme'] = old['acme']['id']

        new = old.copy()

        new.update(data)

        if new['name'] != old['name'] or old['revoked'] != new['revoked']:

            verrors = ValidationErrors()

            await validate_cert_name(
                self.middleware, data['name'], self._config.datastore, verrors, 'certificate_update.name'
            )

            if verrors:
                raise verrors

            await self.middleware.call(
                'datastore.update',
                self._config.datastore,
                id,
                {'name': new['name']},
                {'prefix': self._config.datastore_prefix}
            )

            await self.middleware.call('service.start', 'ssl')

        job.set_progress(90, 'Finalizing changes')

        return await self._get_instance(id)

    @private
    async def delete_domains_authenticator(self, auth_id):
        # Delete provided auth_id from all ACME based certs domains_authenticators
        for cert in await self.query([['acme', '!=', None]]):
            if auth_id in cert['domains_authenticators'].values():
                await self.middleware.call(
                    'datastore.update',
                    self._config.datastore,
                    cert['id'],
                    {
                        'domains_authenticators': {
                            k: v for k, v in cert['domains_authenticators'].items()
                            if v != auth_id
                        }
                    },
                    {'prefix': self._config.datastore_prefix}
                )

    @accepts(
        Int('id'),
        Bool('force', default=False)
    )
    @job(lock='cert_delete')
    def do_delete(self, job, id, force=False):
        """
        Delete certificate of `id`.

        If the certificate is an ACME based certificate, certificate service will try to
        revoke the certificate by updating it's status with the ACME server, if it fails an exception is raised
        and the certificate is not deleted from the system. However, if `force` is set to True, certificate is deleted
        from the system even if some error occurred while revoking the certificate with the ACME Server

        .. examples(websocket)::

          Delete certificate of `id`

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "certificate.delete",
                "params": [
                    1,
                    true
                ]
            }
        """
        verrors = ValidationErrors()

        # Let's make sure we don't delete a certificate which is being used by any service in the system
        for service_cert_id, text in [
            ((self.middleware.call_sync('system.general.config'))['ui_certificate']['id'], 'WebUI'),
            ((self.middleware.call_sync('ftp.config'))['ssltls_certificate'], 'FTP'),
            ((self.middleware.call_sync('s3.config'))['certificate'], 'S3'),
            ((self.middleware.call_sync('webdav.config'))['certssl'], 'Webdav'),
            ((self.middleware.call_sync('openvpn.server.config'))['certificate'], 'OpenVPN Server'),
            ((self.middleware.call_sync('openvpn.client.config'))['certificate'], 'OpenVPN Client')
        ]:
            if service_cert_id == id:
                verrors.add(
                    'certificate_delete.id',
                    f'Selected certificate is being used by {text} service, please select another one'
                )

        verrors.check()

        certificate = self.middleware.call_sync('certificate._get_instance', id)

        if certificate.get('acme'):
            client, key = self.get_acme_client_and_key(certificate['acme']['directory'], True)

            try:
                client.revoke(
                    jose.ComparableX509(
                        crypto.load_certificate(crypto.FILETYPE_PEM, certificate['certificate'])
                    ),
                    0
                )
            except (errors.ClientError, messages.Error) as e:
                if not force:
                    raise CallError(f'Failed to revoke certificate: {e}')

        response = self.middleware.call_sync(
            'datastore.delete',
            self._config.datastore,
            id
        )

        self.middleware.call_sync('service.start', 'ssl')

        job.set_progress(100)
        return response


class CertificateAuthorityService(CRUDService):

    class Config:
        datastore = 'system.certificateauthority'
        datastore_extend = 'certificate.cert_extend'
        datastore_prefix = 'cert_'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.map_create_functions = {
            'CA_CREATE_INTERNAL': self.__create_internal,
            'CA_CREATE_IMPORTED': self.__create_imported_ca,
            'CA_CREATE_INTERMEDIATE': self.__create_intermediate_ca,
        }

    # HELPER METHODS

    @private
    async def validate_common_attributes(self, data, schema_name):
        verrors = ValidationErrors()

        await _validate_common_attributes(self.middleware, data, verrors, schema_name)

        return verrors

    @private
    async def get_serial_for_certificate(self, ca_id):

        ca_data = await self._get_instance(ca_id)

        if ca_data.get('signedby'):
            # Recursively call the same function for it's parent and let the function gather all serials in a chain
            return await self.get_serial_for_certificate(ca_data['signedby']['id'])
        else:

            async def cert_serials(ca_id):
                return [
                    data['serial'] for data in
                    await self.middleware.call(
                        'datastore.query',
                        'system.certificate',
                        [('signedby', '=', ca_id)],
                        {
                            'prefix': self._config.datastore_prefix,
                            'extend': self._config.datastore_extend
                        }
                    )
                ]

            ca_signed_certs = await cert_serials(ca_id)

            async def child_serials(ca_id):
                serials = []
                children = await self.middleware.call(
                    'datastore.query',
                    self._config.datastore,
                    [('signedby', '=', ca_id)],
                    {
                        'prefix': self._config.datastore_prefix,
                        'extend': self._config.datastore_extend
                    }
                )

                for child in children:
                    serials.extend((await child_serials(child['id'])))

                serials.extend((await cert_serials(ca_id)))
                serials.append((await self._get_instance(ca_id))['serial'])

                return serials

            ca_signed_certs.extend((await child_serials(ca_id)))

            # This is for a case where the user might have a malformed certificate and serial value returns None
            ca_signed_certs = list(filter(None, ca_signed_certs))

            if not ca_signed_certs:
                return int(
                    (await self._get_instance(ca_id))['serial'] or 0
                ) + 1
            else:
                return max(ca_signed_certs) + 1

    def _set_enum(name):
        def set_enum(attr):
            attr.enum = ['CA_CREATE_INTERNAL', 'CA_CREATE_IMPORTED', 'CA_CREATE_INTERMEDIATE']
        return {'name': name, 'method': set_enum}

    # CREATE METHODS FOR CREATING CERTIFICATE AUTHORITIES
    # "do_create" IS CALLED FIRST AND THEN BASED ON THE TYPE OF CA WHICH IS TO BE CREATED, THE
    # APPROPRIATE METHOD IS CALLED
    # FOLLOWING TYPES ARE SUPPORTED
    # CREATE_TYPE ( STRING )      - METHOD CALLED
    # CA_CREATE_INTERNAL          - __create_internal
    # CA_CREATE_IMPORTED          - __create_imported_ca
    # CA_CREATE_INTERMEDIATE      - __create_intermediate_ca

    @accepts(
        Patch(
            'certificate_create', 'ca_create',
            ('edit', _set_enum('create_type')),
            ('rm', {'name': 'dns_mapping'}),
            register=True
        )
    )
    async def do_create(self, data):
        """
        Create a new Certificate Authority

        Certificate Authorities are classified under following types with the necessary keywords to be passed
        for `create_type` attribute to create the respective type of certificate authority

        1) Internal Certificate Authority       -  CA_CREATE_INTERNAL

        2) Imported Certificate Authority       -  CA_CREATE_IMPORTED

        3) Intermediate Certificate Authority   -  CA_CREATE_INTERMEDIATE

        Created certificate authorities use RSA keys by default. If an Elliptic Curve Key is desired, then it can be
        specified with the `key_type` attribute. If the `ec_curve` attribute is not specified for the Elliptic
        Curve Key, default to using "BrainpoolP384R1" curve.

        A type is selected by the Certificate Authority Service based on `create_type`. The rest of the values
        are validated accordingly and finally a certificate is made based on the selected type.

        .. examples(websocket)::

          Create an Internal Certificate Authority

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "certificateauthority.create",
                "params": [{
                    "name": "internal_ca",
                    "key_length": 2048,
                    "lifetime": 3600,
                    "city": "Nashville",
                    "common": "domain1.com",
                    "country": "US",
                    "email": "dev@ixsystems.com",
                    "organization": "iXsystems",
                    "state": "Tennessee",
                    "digest_algorithm": "SHA256"
                    "create_type": "CA_CREATE_INTERNAL"
                }]
            }

          Create an Imported Certificate Authority

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "certificateauthority.create",
                "params": [{
                    "name": "imported_ca",
                    "certificate": "Certificate string",
                    "privatekey": "Private key string",
                    "create_type": "CA_CREATE_IMPORTED"
                }]
            }
        """
        create_type = data.pop('create_type')
        if create_type == 'CA_CREATE_IMPORTED':
            for key in ('key_length', 'key_type', 'ec_curve'):
                data.pop(key, None)

        verrors = await self.validate_common_attributes(data, 'certificate_authority_create')

        await validate_cert_name(
            self.middleware, data['name'], self._config.datastore,
            verrors, 'certificate_authority_create.name'
        )

        if verrors:
            raise verrors

        data = await self.map_create_functions[create_type](data)

        data = {
            k: v for k, v in data.items()
            if k in ['name', 'certificate', 'privatekey', 'type', 'signedby', 'revoked']
        }

        pk = await self.middleware.call(
            'datastore.insert',
            self._config.datastore,
            data,
            {'prefix': self._config.datastore_prefix}
        )

        await self.middleware.call('service.start', 'ssl')

        return await self._get_instance(pk)

    @accepts(
        Dict(
            'ca_sign_csr',
            Int('ca_id', required=True),
            Int('csr_cert_id', required=True),
            Str('name', required=True),
            register=True
        )
    )
    async def ca_sign_csr(self, data):
        """
        Sign CSR by Certificate Authority of `ca_id`

        Sign CSR's and generate a certificate from it. `ca_id` provides which CA is to be used for signing
        a CSR of `csr_cert_id` which exists in the system

        .. examples(websocket)::

          Sign CSR of `csr_cert_id` by Certificate Authority of `ca_id`

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "certificateauthority.ca_sign_csr",
                "params": [{
                    "ca_id": 1,
                    "csr_cert_id": 1,
                    "name": "signed_cert"
                }]
            }
        """
        return await self.__ca_sign_csr(data)

    @accepts(
        Ref('ca_sign_csr'),
        Str('schema_name', default='certificate_authority_update')
    )
    async def __ca_sign_csr(self, data, schema_name):
        verrors = ValidationErrors()

        ca_data = await self.query([('id', '=', data['ca_id'])])
        csr_cert_data = await self.middleware.call('certificate.query', [('id', '=', data['csr_cert_id'])])

        if not ca_data:
            verrors.add(
                f'{schema_name}.ca_id',
                f'No Certificate Authority found for id {data["ca_id"]}'
            )
        else:
            ca_data = ca_data[0]
            if not ca_data.get('privatekey'):
                verrors.add(
                    f'{schema_name}.ca_id',
                    'Please use a CA which has a private key assigned'
                )

            if ca_data.get('revoked'):
                verrors.add(
                    f'{schema_name}.ca_id',
                    'Please use a CA which has not been revoked.'
                )

        if not csr_cert_data:
            verrors.add(
                f'{schema_name}.csr_cert_id',
                f'No Certificate found for id {data["csr_cert_id"]}'
            )
        else:
            csr_cert_data = csr_cert_data[0]
            if not csr_cert_data.get('CSR'):
                verrors.add(
                    f'{schema_name}.csr_cert_id',
                    'No CSR has been filed by this certificate'
                )
            else:
                if not await self.middleware.call('cryptokey.load_certificate_request', csr_cert_data['CSR']):
                    verrors.add(
                        f'{schema_name}.csr_cert_id',
                        'CSR not valid'
                    )

        if verrors:
            raise verrors

        serial = await self.get_serial_for_certificate(ca_data['id'])

        new_cert = await self.middleware.call(
            'cryptokey.sign_csr_with_ca',
            {
                'ca_certificate': ca_data['certificate'],
                'ca_privatekey': ca_data['privatekey'],
                'csr': csr_cert_data['CSR'],
                'csr_privatekey': csr_cert_data['privatekey'],
                'serial': serial,
                'digest_algorithm': ca_data['digest_algorithm']
            }
        )

        new_csr = {
            'type': CERT_TYPE_INTERNAL,
            'name': data['name'],
            'certificate': new_cert,
            'privatekey': csr_cert_data['privatekey'],
            'signedby': ca_data['id']
        }

        new_csr_id = await self.middleware.call(
            'datastore.insert',
            'system.certificate',
            new_csr,
            {'prefix': 'cert_'}
        )

        return await self.middleware.call(
            'certificate.query',
            [['id', '=', new_csr_id]],
            {'get': True}
        )

    @accepts(
        Patch(
            'ca_create_internal', 'ca_create_intermediate',
            ('add', {'name': 'signedby', 'type': 'int', 'required': True}),
        ),
    )
    async def __create_intermediate_ca(self, data):

        signing_cert = await self._get_instance(data['signedby'])

        serial = await self.get_serial_for_certificate(signing_cert['id'])

        data['type'] = CA_TYPE_INTERMEDIATE

        cert_info = get_cert_info_from_data(data)
        cert_info.update({
            'ca_privatekey': signing_cert['privatekey'],
            'ca_certificate': signing_cert['certificate'],
            'serial': serial
        })

        cert, key = await self.middleware.call(
            'cryptokey.generate_certificate_authority',
            cert_info
        )

        data['certificate'] = cert
        data['privatekey'] = key

        return data

    @accepts(
        Patch(
            'ca_create', 'ca_create_imported',
            ('edit', _set_required('certificate')),
            ('rm', {'name': 'create_type'}),
        )
    )
    async def __create_imported_ca(self, data):
        data['type'] = CA_TYPE_EXISTING

        if all(k in data for k in ('passphrase', 'privatekey')):
            data['privatekey'] = await self.middleware.call(
                'cryptokey.export_private_key',
                data['privatekey'],
                data['passphrase']
            )

        return data

    @accepts(
        Patch(
            'ca_create', 'ca_create_internal',
            ('edit', _set_required('digest_algorithm')),
            ('edit', _set_required('lifetime')),
            ('edit', _set_required('country')),
            ('edit', _set_required('state')),
            ('edit', _set_required('city')),
            ('edit', _set_required('organization')),
            ('edit', _set_required('email')),
            ('edit', _set_required('common')),
            ('rm', {'name': 'create_type'}),
            register=True
        )
    )
    async def __create_internal(self, data):
        cert_info = get_cert_info_from_data(data)
        cert_info['serial'] = random.getrandbits(24)

        (cert, key) = await self.middleware.call(
            'cryptokey.generate_self_signed_ca',
            cert_info
        )

        data['type'] = CA_TYPE_INTERNAL
        data['certificate'] = cert
        data['privatekey'] = key

        return data

    @private
    async def revoke_ca_chain(self, ca_id):
        for cert in await self.middleware.call(
            'datastore.query',
            'system.certificate',
            [('signedby', '=', ca_id)],
            {'prefix': self._config.datastore_prefix}
        ):
            await self.middleware.call('certificate.update', cert['id'], {'revoked': True})

        for ca in await self.middleware.call(
            'datastore.query',
            self._config.datastore,
            [('signedby', '=', ca_id)],
            {'prefix': self._config.datastore_prefix}
        ):
            await self.revoke_ca_chain(ca['id'])

        await self.middleware.call(
            'datastore.update',
            self._config.datastore,
            ca_id,
            {'revoked': True},
            {'prefix': self._config.datastore_prefix}
        )

    @accepts(
        Int('id', required=True),
        Dict(
            'ca_update',
            Bool('revoked'),
            Int('ca_id'),
            Int('csr_cert_id'),
            Str('create_type', enum=['CA_SIGN_CSR']),
            Str('name'),
        )
    )
    async def do_update(self, id, data):
        """
        Update Certificate Authority of `id`

        Only name attribute can be updated

        .. examples(websocket)::

          Update a Certificate Authority of `id`

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "certificateauthority.update",
                "params": [
                    1,
                    {
                        "name": "updated_ca_name"
                    }
                ]
            }
        """
        if data.pop('create_type', '') == 'CA_SIGN_CSR':
            # BEING USED BY OLD LEGACY FOR SIGNING CSR'S. THIS CAN BE REMOVED WHEN LEGACY UI IS REMOVED
            data['ca_id'] = id
            return await self.__ca_sign_csr(data, 'certificate_authority_update')
        else:
            for key in ['ca_id', 'csr_cert_id']:
                data.pop(key, None)

        old = await self._get_instance(id)
        # signedby is changed back to integer from a dict
        old['signedby'] = old['signedby']['id'] if old.get('signedby') else None

        new = old.copy()
        new.update(data)

        verrors = ValidationErrors()

        if new['name'] != old['name'] or old['revoked'] != new['revoked']:
            await validate_cert_name(
                self.middleware, data['name'], self._config.datastore, verrors, 'certificate_authority_update.name'
            )

            if verrors:
                raise verrors

            await self.middleware.call(
                'datastore.update',
                self._config.datastore,
                id,
                {'name': new['name']},
                {'prefix': self._config.datastore_prefix}
            )

            if old['revoked'] != new['revoked'] and new['revoked']:
                await self.revoke_ca_chain(id)

            await self.middleware.call('service.start', 'ssl')

        return await self._get_instance(id)

    @accepts(
        Int('id')
    )
    async def do_delete(self, id):
        """
        Delete a Certificate Authority of `id`

        .. examples(websocket)::

          Delete a Certificate Authority of `id`

            :::javascript
            {
                "id": "6841f242-840a-11e6-a437-00e04d680384",
                "msg": "method",
                "method": "certificateauthority.delete",
                "params": [
                    1
                ]
            }
        """
        verrors = ValidationErrors()

        # Let's make sure we don't delete a ca which is being used by any service in the system
        for service_cert_id, text in [
            ((await self.middleware.call_sync('openvpn.server.config'))['certificate'], 'OpenVPN Server'),
            ((await self.middleware.call_sync('openvpn.client.config'))['certificate'], 'OpenVPN Client')
        ]:
            if service_cert_id == id:
                verrors.add(
                    'certificateauthority_delete.id',
                    f'Selected certificate authority is being used by {text} service, please select another one.'
                )

        verrors.check()

        response = await self.middleware.call(
            'datastore.delete',
            self._config.datastore,
            id
        )

        await self.middleware.call('service.start', 'ssl')

        return response


async def setup(middlewared):
    system_cert = (await middlewared.call('system.general.config'))['ui_certificate']
    certs = await middlewared.call('certificate.query')
    if not system_cert or system_cert['id'] not in [c['id'] for c in certs]:
        # create a self signed cert if it doesn't exist and set ui_certificate to it's value
        try:
            if not any('freenas_default' == c['name'] for c in certs):
                cert, key = await middlewared.call('cryptokey.generate_self_signed_certificate')

                cert_dict = {
                    'certificate': cert,
                    'privatekey': key,
                    'name': 'freenas_default',
                    'type': CERT_TYPE_EXISTING,
                }

                # We use datastore.insert to directly insert in db as jobs cannot be waited for at this point
                id = await middlewared.call(
                    'datastore.insert',
                    'system.certificate',
                    cert_dict,
                    {'prefix': 'cert_'}
                )

                await middlewared.call('service.start', 'ssl')

                middlewared.logger.debug('Default certificate for System created')
            else:
                id = [c['id'] for c in certs if c['name'] == 'freenas_default'][0]

            await middlewared.call('system.general.update', {'ui_certificate': id})
        except Exception as e:
            middlewared.logger.debug(f'Failed to set certificate for system.general plugin: {e}')

    middlewared.logger.debug('Certificate setup for System complete')
