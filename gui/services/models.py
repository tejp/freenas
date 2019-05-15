# +
# Copyright 2010 iXsystems, Inc.
# All rights reserved
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted providing that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
# IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
#####################################################################
import hashlib
import hmac
import logging
import subprocess
import uuid

from django.db import models
from django.utils.translation import ugettext_lazy as _
from django.core.validators import (
    MinValueValidator, MaxValueValidator
)

from freenasUI import choices
from freenasUI.freeadmin.models import (
    Model, UserField, GroupField, PathField, DictField, ListField
)
from freenasUI.freeadmin.models.fields import MultiSelectField
from freenasUI.middleware.client import client
from freenasUI.middleware.notifier import notifier
from freenasUI.storage.models import Disk
from freenasUI.system.models import Certificate, CertificateAuthority

log = logging.getLogger("services.forms")


class services(Model):
    srv_service = models.CharField(
        max_length=120,
        verbose_name=_("Service"),
        help_text=_("Name of Service, should be auto-generated at build "
                    "time"),
    )
    srv_enable = models.BooleanField(
        verbose_name=_("Enable Service"),
        default=False,
    )

    class Meta:
        verbose_name = _("Services")
        verbose_name_plural = _("Services")

    def __str__(self):
        return self.srv_service


class CIFS(Model):
    cifs_srv_netbiosname = models.CharField(
        max_length=120,
        verbose_name=_("NetBIOS name"),
    )
    cifs_srv_netbiosname_b = models.CharField(
        max_length=120,
        verbose_name=_("NetBIOS name"),
        blank=True,
        null=True,
    )
    cifs_srv_netbiosalias = models.CharField(
        max_length=120,
        verbose_name=_("NetBIOS alias"),
        blank=True,
        null=True,
    )
    cifs_srv_workgroup = models.CharField(
        max_length=120,
        verbose_name=_("Workgroup"),
        help_text=_("Workgroup the server will appear to be in when "
                    "queried by clients (maximum 15 characters)."),
    )
    cifs_srv_description = models.CharField(
        max_length=120,
        verbose_name=_("Description"),
        blank=True,
        help_text=_("Server description. This can usually be left blank."),
    )
    cifs_srv_enable_smb1 = models.BooleanField(
        verbose_name=_("Enable SMB1 support"),
        default=False,
        help_text=_(
            "Use this option to allow legacy SMB clients to connect to the "
            "server. Note that SMB1 is being deprecated and it is advised "
            "to upgrade clients to operating system versions that support "
            "modern versions of the SMB protocol."
        ),
    )
    cifs_srv_unixcharset = models.CharField(
        max_length=120,
        default="UTF-8",
        verbose_name=_("UNIX charset"),
    )
    cifs_srv_loglevel = models.CharField(
        max_length=120,
        choices=choices.LOGLEVEL_CHOICES,
        default=choices.LOGLEVEL_CHOICES[0][0],
        verbose_name=_("Log level"),
    )
    cifs_srv_syslog = models.BooleanField(
        verbose_name=_("Use syslog only"),
        default=False,
    )
    cifs_srv_localmaster = models.BooleanField(
        verbose_name=_("Local Master"),
        default=False,
    )
    cifs_srv_domain_logons = models.BooleanField(
        verbose_name=_("Domain logons"),
        default=False,
    )
    cifs_srv_timeserver = models.BooleanField(
        verbose_name=_("Time Server for Domain"),
        default=False,
    )
    cifs_srv_guest = UserField(
        max_length=120,
        default="nobody",
        exclude=["root"],
        verbose_name=_("Guest account"),
        help_text=_(
            "Use this option to override the username ('nobody' by default) "
            "which will be used for access to services which are specified as "
            "guest. Whatever privileges this user has will be available to "
            "any client connecting to the guest service. This user must exist "
            "in the password file, but does not require a valid login. The "
            "user root cannot be used as guest account."
        ),
    )
    cifs_srv_admin_group = GroupField(
        max_length=120,
        default="",
        blank=True,
        verbose_name=_("Administrators Group"),
        help_text=_(
            'Members of this group are local admins and automatically '
            'have privileges to take ownership of any file in an SMB '
            'share, reset permissions, and administer the SMB server '
            'through the Computer Management MMC snap-in.'
        ),
    )
    cifs_srv_filemask = models.CharField(
        max_length=120,
        verbose_name=_("File mask"),
        blank=True,
        help_text=_("Use this option to override the file creation mask "
                    "(0666 by default)."),
    )
    cifs_srv_dirmask = models.CharField(
        max_length=120,
        verbose_name=_("Directory mask"),
        blank=True,
        help_text=_("Use this option to override the directory creation "
                    "mask (0777 by default)."),
    )
    cifs_srv_nullpw = models.BooleanField(
        verbose_name=_("Allow Empty Password"),
        default=False,
    )
    cifs_srv_smb_options = models.TextField(
        verbose_name=_("Auxiliary parameters"),
        blank=True,
        help_text=_("These parameters are added to the [global] section of "
                    "smb.conf"),
    )
    cifs_srv_unixext = models.BooleanField(
        verbose_name=_("Unix Extensions"),
        default=True,
        help_text=_("These extensions enable Samba to better serve UNIX "
                    "SMB clients by supporting features such as symbolic "
                    "links, hard links, etc..."),
    )
    cifs_srv_aio_enable = models.BooleanField(
        default=False,
        verbose_name=_("Enable AIO"),
        editable=False,
        help_text=_("Enable/disable AIO support."),
    )
    cifs_srv_aio_rs = models.IntegerField(
        verbose_name=_("Minimum AIO read size"),
        help_text=_("Samba will read asynchronously if request size is "
                    "larger than this value."),
        default=4096,
        editable=False,
    )
    cifs_srv_aio_ws = models.IntegerField(
        verbose_name=_("Minimum AIO write size"),
        help_text=_("Samba will write asynchronously if request size is "
                    "larger than this value."),
        default=4096,
        editable=False,
    )
    cifs_srv_zeroconf = models.BooleanField(
        verbose_name=_("Zeroconf share discovery"),
        default=True,
        help_text=_("Zeroconf support via Avahi allows clients (the Mac "
                    "OSX finder in particular) to automatically discover the "
                    "SMB shares on the system similar to the Computer "
                    "Browser service in Windows."),
    )
    cifs_srv_hostlookup = models.BooleanField(
        verbose_name=_("Hostnames lookups"),
        default=True,
        help_text=_("Specifies whether Samba should use (expensive) "
                    "hostname lookups or use IP addresses instead. An "
                    "example place where hostname lookups are currently used "
                    "is when checking the hosts deny and hosts allow."),
    )
    cifs_srv_allow_execute_always = models.BooleanField(
        verbose_name=_("Allow execute always"),
        default=True,
        help_text=_("This boolean parameter controls the behaviour of smbd(8) "
                    "when receiving a protocol request of \"open for "
                    "execution\" from a Windows " "client. With Samba 3.6 and "
                    "older, the execution right in the ACL was not checked, "
                    "so a client could execute a file even if it did not have "
                    "execute rights on the file. In Samba 4.0, this has been "
                    "fixed, so that by default, i.e. when this parameter is "
                    "set to " "\"False\", \"open for execution\" is now "
                    "denied when execution " "permissions are not present. If "
                    "this parameter is set to \"True\", Samba does not check "
                    "execute permissions on \"open for execution\", thus "
                    "re-establishing the behavior of Samba 3.6 "),
    )
    cifs_srv_obey_pam_restrictions = models.BooleanField(
        verbose_name=_("Obey pam restrictions"),
        default=True,
        help_text=_("This parameter controls whether or not Samba should obey "
                    "PAM's account and session management directives"),
    )
    cifs_srv_ntlmv1_auth = models.BooleanField(
        verbose_name=_("NTLMv1 auth"),
        default=False,
        help_text=_("Off by default. When set, smbd(8) attempts "
                    "to authenticate users with the insecure "
                    "and vulnerable NTLMv1 encryption. This setting "
                    "allows backward compatibility with older "
                    "versions of Windows, but is not "
                    "recommended and should not be used on untrusted "
                    "networks.")
    )
    cifs_srv_bindip = MultiSelectField(
        verbose_name=_("Bind IP Addresses"),
        help_text=_("IP addresses to bind to. If none specified, all "
                    "available interfaces that are up will be listened on."),
        max_length=250,
        blank=True,
        null=True,
    )
    cifs_SID = models.CharField(
        max_length=120,
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = _("SMB")
        verbose_name_plural = _("SMB")

    class FreeAdmin:
        deletable = False
        icon_model = "CIFSIcon"

    def get_netbiosname(self):
        _n = notifier()
        if not _n.is_freenas() and _n.failover_node() == 'B':
            return self.cifs_srv_netbiosname_b
        else:
            return self.cifs_srv_netbiosname


class AFP(Model):
    afp_srv_guest = models.BooleanField(
        verbose_name=_("Guest Access"),
        help_text=_("Allows guest access to all Apple shares on this box."),
        default=False,
    )
    afp_srv_guest_user = UserField(
        max_length=120,
        default="nobody",
        exclude=["root"],
        verbose_name=_("Guest account"),
        help_text=_("Use this option to override the username ('nobody' by "
                    "default) which will be used for access to services which "
                    "are specified as guest. Whatever privileges this user "
                    "has will be available to any client connecting to the "
                    "guest service. This user must exist in the password "
                    "file, but does not require a valid login. The user root "
                    "cannot be used as guest account."),
    )
    afp_srv_bindip = MultiSelectField(
        verbose_name=_("Bind IP Addresses"),
        help_text=_(
            "IP addresses to advertise and listens to. If none specified, "
            "advertise the first IP address of the system, but to listen for "
            "any incoming request."
        ),
        max_length=255,
        blank=True,
        default='',
    )
    afp_srv_connections_limit = models.IntegerField(
        verbose_name=_('Max. Connections'),
        validators=[MinValueValidator(1), MaxValueValidator(1000)],
        help_text=_("Maximum number of connections permitted via AFP. The "
                    "default limit is 50."),
        default=50,
    )
    afp_srv_dbpath = PathField(
        verbose_name=_('Database Path'),
        blank=True,
        help_text=_(
            'Sets the database information to be stored in path. You have to '
            'specify a writable location, even if the volume is read only.'),
    )
    afp_srv_global_aux = models.TextField(
        verbose_name=_("Global auxiliary parameters"),
        blank=True,
        help_text=_(
            "These parameters are added to the [Global] section of afp.conf"),
    )
    afp_srv_map_acls = models.CharField(
        verbose_name=_("Map ACLs"),
        max_length=120,
        help_text=_("How to map the effective permissions of authenticated users: "
                    "Rights (default, Unix-style permissions), "
                    "Mode (ACLs), "
                    "or None"),
        choices=choices.AFP_MAP_ACLS_CHOICES,
        default='rights'
    )
    afp_srv_chmod_request = models.CharField(
        verbose_name=_("Chmod Request"),
        max_length=120,
        help_text=_(
            "Advanced permission control that deals with ACLs."
            "\nignore - UNIX chmod() requests are completely ignored, use this"
            "option to allow the parent directory's ACL inheritance full"
            "control over new items."
            "\npreserve - preserve ZFS ACEs for named users and groups or"
            "POSIX ACL group mask"
            "\nsimple - just to a chmod() as requested without any extra steps"
        ),
        choices=choices.AFP_CHMOD_REQUEST_CHOICES,
        default='preserve'
    )

    class Meta:
        verbose_name = _("AFP")
        verbose_name_plural = _("AFP")

    class FreeAdmin:
        deletable = False
        icon_model = "AFPIcon"


class NFS(Model):
    nfs_srv_servers = models.PositiveIntegerField(
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(256)],
        verbose_name=_("Number of servers"),
        help_text=_("Specifies how many servers to create. There should be "
                    "enough to handle the maximum level of concurrency from "
                    "clients, typically four to six."),
    )
    nfs_srv_udp = models.BooleanField(
        verbose_name=_('Serve UDP NFS clients'),
        default=False,
    )
    nfs_srv_allow_nonroot = models.BooleanField(
        default=False,
        verbose_name=_("Allow non-root mount"),
        help_text=_("Allow non-root mount requests to be served. This should "
                    "only be specified if there are clients that require it. "
                    "It will automatically clear the vfs.nfsrv.nfs_privport "
                    "sysctl flag, which controls if the kernel will accept "
                    "NFS requests from reserved ports only."),
    )
    nfs_srv_v4 = models.BooleanField(
        default=False,
        verbose_name=_("Enable NFSv4"),
    )
    nfs_srv_v4_v3owner = models.BooleanField(
        default=False,
        verbose_name=_("NFSv3 ownership model for NFSv4"),
        help_text=_("Use the NFSv3 ownership model for NFSv4.  This "
                    "circumvents the need to sync users and groups "
                    "between the client and server. Note that this "
                    "option is mutually incompatible with the > 16 "
                    "groups option."),
    )
    nfs_srv_v4_krb = models.BooleanField(
        default=False,
        verbose_name=_("Require Kerberos for NFSv4"),
    )
    nfs_srv_bindip = MultiSelectField(
        blank=True,
        max_length=250,
        verbose_name=_("Bind IP Addresses"),
        choices=choices.IPChoices(),
        help_text=_("Select the IP addresses to listen to for NFS requests. "
                    "If left unchecked, NFS will listen on all available "
                    "addresses."),
    )
    nfs_srv_mountd_port = models.SmallIntegerField(
        verbose_name=_("mountd(8) bind port"),
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        blank=True,
        null=True,
        help_text=_(
            "Force mountd to bind to the specified port, for both IPv4 and "
            "IPv6 address families. This is typically done to ensure that "
            "the port which mountd binds to is a known value which can be "
            "used in firewall rulesets."),
    )
    nfs_srv_rpcstatd_port = models.SmallIntegerField(
        verbose_name=_("rpc.statd(8) bind port"),
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        blank=True,
        null=True,
        help_text=_(
            "Forces the rpc.statd daemon to bind to the specified "
            "port, for both IPv4 and IPv6 address families."),
    )
    nfs_srv_rpclockd_port = models.SmallIntegerField(
        verbose_name=_("rpc.lockd(8) bind port"),
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        blank=True,
        null=True,
        help_text=_(
            "Force the rpc.lockd daemon to bind to the specified "
            "port, for both IPv4 and IPv6 address families."),
    )
    nfs_srv_16 = models.BooleanField(
        default=False,
        verbose_name=_("Support >16 groups"),
        help_text=_(
            "Ignore the group membership sent on the wire by the "
            "NFS client and look up the group membership on the server.  Note "
            "that this option is mutually incompatible with the NFSv3 ownership "
            "model for NFSv4."),
    )
    nfs_srv_mountd_log = models.BooleanField(
        default=True,
        verbose_name=_("Log mountd(8) requests"),
        help_text=_(
            "Enable/disable mountd logging into syslog."),
    )
    nfs_srv_statd_lockd_log = models.BooleanField(
        default=False,
        verbose_name=_("Log rpc.statd(8) and rpc.lockd(8)"),
        help_text=_(
            "Enable/disable statd and lockd logging into syslog."),
    )

    class Meta:
        verbose_name = _("NFS")
        verbose_name_plural = _("NFS")


class iSCSITargetGlobalConfiguration(Model):
    iscsi_basename = models.CharField(
        max_length=120,
        verbose_name=_("Base Name"),
        help_text=_("The base name (e.g. iqn.2005-10.org.freenas.ctl, "
                    "see RFC 3720 and 3721 for details) will append the "
                    "target " "name that is not starting with 'iqn.', "
                    "'eui.' or 'naa.'"),
    )
    iscsi_isns_servers = models.TextField(
        verbose_name=_('iSNS Servers'),
        blank=True,
        help_text=_("List of Internet Storage Name Service (iSNS) Servers"),
    )
    iscsi_pool_avail_threshold = models.IntegerField(
        verbose_name=_('Pool Available Space Threshold (%)'),
        blank=True,
        null=True,
        validators=[MinValueValidator(1), MaxValueValidator(99)],
        help_text=_(
            "Remaining ZFS pool capacity warning threshold when using zvol "
            "extents"
        ),
    )
    iscsi_alua = models.BooleanField(
        verbose_name=_('Enable iSCSI ALUA'),
        default=False,
        help_text=_('Enabling this feature requires initiator reconfiguration'),
    )

    class Meta:
        verbose_name = _("Target Global Configuration")
        verbose_name_plural = _("Target Global Configuration")

    class FreeAdmin:
        deletable = False
        menu_child_of = "sharing.ISCSI"
        icon_model = "SettingsIcon"
        nav_extra = {'type': 'iscsi', 'order': -10}
        resource_name = 'services/iscsi/globalconfiguration'


def extent_serial():
    try:
        nic = list(choices.NICChoices(nolagg=True,
                                      novlan=True,
                                      exclude_configured=False,
                                      include_lagg_parent=False))[0][0]
        mac = subprocess.Popen(
            "ifconfig %s ether| grep ether | "
            "awk '{print $2}'|tr -d :" % (nic, ),
            shell=True,
            stdout=subprocess.PIPE,
            encoding='utf8').communicate()[0]
        ltg = iSCSITargetExtent.objects.order_by('-id')
        if ltg.count() > 0:
            lid = ltg[0].id
        else:
            lid = 0
        return mac.strip() + "%.2d" % lid
    except Exception:
        return "10000001"


class iSCSITargetExtent(Model):
    iscsi_target_extent_name = models.CharField(
        max_length=120,
        unique=True,
        verbose_name=_("Extent Name"),
        help_text=_("String identifier of the extent."),
    )
    iscsi_target_extent_serial = models.CharField(
        verbose_name=_("Serial"),
        max_length=16,
        default=extent_serial,
        help_text=_("Serial number for the logical unit")
    )
    iscsi_target_extent_type = models.CharField(
        max_length=120,
        verbose_name=_("Extent Type"),
        help_text=_("Type used as extent."),
    )
    iscsi_target_extent_path = models.CharField(
        max_length=120,
        verbose_name=_("Path to the extent"),
        help_text=_("File path (e.g. /mnt/sharename/extent/extent0) "
                    "used as extent."),
    )
    iscsi_target_extent_filesize = models.CharField(
        max_length=120,
        default=0,
        verbose_name=_("Extent size"),
        help_text=_("Size of extent: 0 means auto, a raw number is bytes"
                    ", or suffix with KB, MB, or TB for convenience."),
    )
    iscsi_target_extent_blocksize = models.IntegerField(
        choices=choices.TARGET_BLOCKSIZE_CHOICES,
        default=choices.TARGET_BLOCKSIZE_CHOICES[0][0],
        verbose_name=_("Logical Block Size"),
        help_text=_("Logical block length (512 by "
                    "default). The recommended length for compatibility is "
                    "512."),
    )
    iscsi_target_extent_pblocksize = models.BooleanField(
        default=False,
        verbose_name=_("Disable Physical Block Size Reporting"),
        help_text=_(
            'By default, the physical blocksize is reported as the ZFS block '
            'size, which can be up to 128K. Some initiators do not work with '
            'values above 4K. Checking this disables reporting the physical '
            'blocksize.'),
    )
    iscsi_target_extent_avail_threshold = models.IntegerField(
        verbose_name=_('Available Space Threshold (%)'),
        blank=True,
        null=True,
        validators=[MinValueValidator(1), MaxValueValidator(99)],
        help_text=_("Remaining dataset/zvol capacity warning threshold"),
    )
    iscsi_target_extent_comment = models.CharField(
        blank=True,
        max_length=120,
        verbose_name=_("Comment"),
        help_text=_("A description can be entered here for your "
                    "reference."),
    )
    iscsi_target_extent_naa = models.CharField(
        blank=True,
        editable=False,
        unique=True,
        max_length=34,
        verbose_name=_("NAA...used only by the initiator"),
    )
    iscsi_target_extent_insecure_tpc = models.BooleanField(
        default=True,
        verbose_name=_("Enable TPC"),
        help_text=_("Allow initiators to xcopy without authenticating to "
                    "foreign targets."),
    )
    iscsi_target_extent_xen = models.BooleanField(
        default=False,
        verbose_name=_("Xen initiator compat mode"),
        help_text=_("Xen inititors give errors when connecting to LUNs using "
                    "the FreeNAS default naming scheme.  Checking this alters "
                    "the naming scheme to be more Xen-friendly"),
    )
    iscsi_target_extent_rpm = models.CharField(
        blank=False,
        max_length=20,
        default='SSD',
        choices=choices.EXTENT_RPM_CHOICES,
        verbose_name=_("LUN RPM"),
        help_text=_("RPM reported to initiators for this extent/LUN. The "
                    "default is SSD because Windows will attempt to defrag "
                    "non SSD devices.  This is a pathological worst-case "
                    "situation for ZFS.  VMWare gives the option to "
                    "use SSD " "LUNs as swap devices. There is some value to "
                    "picking a non-SSD RPM if your " "extent is indeed not "
                    "SSDs and the initiator will be VMWare."),
    )
    iscsi_target_extent_ro = models.BooleanField(
        default=False,
        verbose_name=_("Read-only"),
    )
    iscsi_target_extent_legacy = models.BooleanField(
        default=False,
    )

    class Meta:
        verbose_name = _("Extent")
        verbose_name_plural = _("Extents")
        ordering = ["iscsi_target_extent_name"]

    def __str__(self):
        return str(self.iscsi_target_extent_name)

    def get_device(self):
        if self.iscsi_target_extent_type not in ("Disk", "ZVOL"):
            return self.iscsi_target_extent_path
        else:
            try:
                disk = Disk.objects.get(pk=self.iscsi_target_extent_path)
                if disk.disk_multipath_name:
                    return "/dev/%s" % disk.devname
                else:
                    with client as c:
                        return "/dev/%s" % (
                            c.call('disk.identifier_to_device', disk.disk_identifier),
                        )
            except Exception:
                return self.iscsi_target_extent_path


class iSCSITargetPortal(Model):
    iscsi_target_portal_tag = models.IntegerField(
        default=1,
        verbose_name=_("Portal Group ID"),
    )
    iscsi_target_portal_comment = models.CharField(
        max_length=120,
        blank=True,
        verbose_name=_("Comment"),
        help_text=_("A description can be entered here for your reference."),
    )
    iscsi_target_portal_discoveryauthmethod = models.CharField(
        max_length=120,
        choices=choices.AUTHMETHOD_CHOICES,
        default='None',
        verbose_name=_("Discovery Auth Method")
    )
    iscsi_target_portal_discoveryauthgroup = models.IntegerField(
        verbose_name=_("Discovery Auth Group"),
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = _("Portal")
        verbose_name_plural = _("Portals")

    def __str__(self):
        if self.iscsi_target_portal_comment != "":
            return "%s (%s)" % (
                self.iscsi_target_portal_tag,
                self.iscsi_target_portal_comment,
            )
        else:
            return str(self.iscsi_target_portal_tag)


class iSCSITargetPortalIP(Model):
    iscsi_target_portalip_portal = models.ForeignKey(
        iSCSITargetPortal,
        verbose_name=_("Portal"),
        related_name='ips',
    )
    iscsi_target_portalip_ip = models.GenericIPAddressField(
        verbose_name=_("IP Address"),
    )
    iscsi_target_portalip_port = models.SmallIntegerField(
        verbose_name=_("Port"),
        default=3260,
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
    )

    class Meta:
        unique_together = (
            ('iscsi_target_portalip_ip', 'iscsi_target_portalip_port'),
        )
        verbose_name = _("Portal IP")
        verbose_name_plural = _("Portal IPs")

    def __str__(self):
        return "%s:%d" % (
            self.iscsi_target_portalip_ip,
            self.iscsi_target_portalip_port,
        )

    def alua_ips(self):
        from freenasUI.network.models import Interfaces
        node_a = []
        node_b = []
        if self.iscsi_target_portalip_ip == '0.0.0.0':
            return node_a, node_b
        for iface in Interfaces.objects.all():
            if self.iscsi_target_portalip_ip == iface.int_vip:
                if iface.int_ipv4address:
                    node_a.append('{}:{}'.format(iface.int_ipv4address, self.iscsi_target_portalip_port))
                if iface.int_ipv4address_b:
                    node_b.append('{}:{}'.format(iface.int_ipv4address_b, self.iscsi_target_portalip_port))
                break
            for alias in iface.alias_set.all():
                if self.iscsi_target_portalip_ip == alias.alias_vip:
                    if alias.alias_v4address:
                        node_a.append('{}:{}'.format(alias.alias_v4address, self.iscsi_target_portalip_port))
                    if alias.alias_v4address_b:
                        node_b.append('{}:{}'.format(alias.alias_v4address_b, self.iscsi_target_portalip_port))
                    break
        return node_a, node_b


class iSCSITargetAuthorizedInitiator(Model):
    iscsi_target_initiator_tag = models.IntegerField(
        default=1,
        unique=True,
        verbose_name=_("Group ID"),
    )
    iscsi_target_initiator_initiators = models.TextField(
        max_length=2048,
        verbose_name=_("Initiators"),
        default="ALL",
        help_text=_("Initiator authorized to access to the iSCSI target. "
                    "It takes a name or 'ALL' for any initiators."),
    )
    iscsi_target_initiator_auth_network = models.TextField(
        max_length=2048,
        verbose_name=_("Authorized network"),
        default="ALL",
        help_text=_("Network authorized to access to the iSCSI target. "
                    "It takes IP or CIDR addresses or 'ALL' for any IPs."),
    )
    iscsi_target_initiator_comment = models.CharField(
        max_length=120,
        blank=True,
        verbose_name=_("Comment"),
        help_text=_("You may enter a description here for your reference."),
    )

    class Meta:
        verbose_name = _("Initiator")
        verbose_name_plural = _("Initiators")

    class FreeAdmin:
        menu_child_of = "sharing.ISCSI"
        icon_object = "InitiatorIcon"
        icon_model = "InitiatorIcon"
        icon_add = "AddInitiatorIcon"
        icon_view = "ViewAllInitiatorsIcon"
        nav_extra = {'order': 0}
        resource_name = 'services/iscsi/authorizedinitiator'

    def __str__(self):
        if self.iscsi_target_initiator_comment != "":
            return "%s (%s)" % (
                self.iscsi_target_initiator_tag,
                self.iscsi_target_initiator_comment,
            )
        else:
            return str(self.iscsi_target_initiator_tag)


class iSCSITargetAuthCredential(Model):
    iscsi_target_auth_tag = models.IntegerField(
        default=1,
        verbose_name=_("Group ID"),
    )
    iscsi_target_auth_user = models.CharField(
        max_length=120,
        verbose_name=_("User"),
        help_text=_("Target side user name. It is usually the initiator "
                    "name by default."),
    )
    iscsi_target_auth_secret = models.CharField(
        max_length=120,
        verbose_name=_("Secret"),
        help_text=_("Target side secret."),
    )
    iscsi_target_auth_peeruser = models.CharField(
        max_length=120,
        blank=True,
        verbose_name=_("Peer User"),
        help_text=_("Initiator side user name."),
    )
    iscsi_target_auth_peersecret = models.CharField(
        max_length=120,
        verbose_name=_("Peer Secret"),
        blank=True,
        help_text=_("Initiator side secret. (for mutual CHAP authentication)"),
    )

    class Meta:
        verbose_name = _("Authorized Access")
        verbose_name_plural = _("Authorized Accesses")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for attr in ('iscsi_target_auth_secret', 'iscsi_target_auth_peersecret'):
            field = getattr(self, attr)
            if field and self.id:
                try:
                    setattr(self, attr, notifier().pwenc_decrypt(field))
                except Exception as e:
                    log.debug(f'Failed to decrypt {attr} password', exc_info=True)
                    setattr(self, attr, '')

    def save(self, *args, **kwargs):
        for attr in ('iscsi_target_auth_secret', 'iscsi_target_auth_peersecret'):
            field = getattr(self, attr)
            if field:
                encrypted_val = notifier().pwenc_encrypt(field)
                setattr(self, attr, encrypted_val)
        return super().save(*args, **kwargs)

    def __str__(self):
        return str(self.iscsi_target_auth_tag)


class iSCSITarget(Model):
    iscsi_target_name = models.CharField(
        unique=True,
        max_length=120,
        verbose_name=_("Target Name"),
        help_text=_("Base Name will be appended automatically when "
                    "starting without 'iqn.', 'eui.' or 'naa.'."),
    )
    iscsi_target_alias = models.CharField(
        unique=True,
        blank=True,
        null=True,
        max_length=120,
        verbose_name=_("Target Alias"),
        help_text=_("Optional user-friendly string of the target."),
    )
    iscsi_target_mode = models.CharField(
        choices=(
            ('iscsi', _('iSCSI')),
            ('fc', _('Fibre Channel')),
            ('both', _('Both')),
        ),
        default='iscsi',
        max_length=20,
        verbose_name=_('Target Mode'),
    )

    class Meta:
        verbose_name = _("Target")
        verbose_name_plural = _("Targets")
        ordering = ['iscsi_target_name']

    def __str__(self):
        return self.iscsi_target_name


class iSCSITargetGroups(Model):
    iscsi_target = models.ForeignKey(
        iSCSITarget,
        verbose_name=_("Target"),
        help_text=_("Target this group belongs to"),
    )
    iscsi_target_portalgroup = models.ForeignKey(
        iSCSITargetPortal,
        verbose_name=_("Portal Group ID"),
    )
    iscsi_target_initiatorgroup = models.ForeignKey(
        iSCSITargetAuthorizedInitiator,
        verbose_name=_("Initiator Group ID"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    iscsi_target_authtype = models.CharField(
        max_length=120,
        choices=choices.AUTHMETHOD_CHOICES,
        default="None",
        verbose_name=_("Auth Method"),
        help_text=_("The authentication method accepted by the target."),
    )
    iscsi_target_authgroup = models.IntegerField(
        verbose_name=_("Authentication Group ID"),
        null=True,
        blank=True,
    )
    iscsi_target_initialdigest = models.CharField(
        max_length=120,
        default="Auto",
        verbose_name=_("Auth Method"),
        help_text=_("The method can be accepted by the target. Auto means "
                    "both none and authentication."),
    )

    def __str__(self):
        return 'iSCSI Target Group (%s,%d)' % (
            self.iscsi_target,
            self.id,
        )

    class Meta:
        verbose_name = _("iSCSI Group")
        verbose_name_plural = _("iSCSI Groups")
        unique_together = (
            ('iscsi_target', 'iscsi_target_portalgroup'),
        )


class iSCSITargetToExtent(Model):
    iscsi_target = models.ForeignKey(
        iSCSITarget,
        verbose_name=_("Target"),
        help_text=_("Target this extent belongs to"),
    )
    iscsi_lunid = models.IntegerField(
        verbose_name=_('LUN ID'),
        null=False,
    )
    iscsi_extent = models.ForeignKey(
        iSCSITargetExtent,
        verbose_name=_("Extent"),
    )

    class Meta:
        ordering = ['iscsi_target', 'iscsi_lunid']
        verbose_name = _("Target / Extent")
        verbose_name_plural = _("Targets / Extents")
        unique_together = (
            'iscsi_target',
            'iscsi_extent',
        )

    def __str__(self):
        return str(self.iscsi_target) + ' / ' + str(self.iscsi_extent)


class FibreChannelToTarget(Model):
    fc_port = models.CharField(
        verbose_name=_('Port'),
        max_length=10,
    )
    fc_target = models.ForeignKey(
        iSCSITarget,
        verbose_name=_("Target"),
        help_text=_("Target this extent belongs to"),
        null=True,
    )

    class Meta:
        verbose_name = _('Fibre Channel Target')
        verbose_name_plural = _('Fibre Channel Targets')


class DynamicDNS(Model):
    ddns_provider = models.CharField(
        max_length=120,
        default='dyndns@3322.org',
        choices=choices.DYNDNSPROVIDER_CHOICES(),
        blank=True,
        verbose_name=_("Provider"),
    )
    ddns_checkip_ssl = models.BooleanField(
        verbose_name=_("CheckIP Server SSL"),
    )
    ddns_checkip_server = models.CharField(
        max_length=150,
        blank=True,
        verbose_name=_('CheckIP Server'),
        help_text=_(
            'The client IP is detected by calling \'url\' from this '
            '\'ip_server_name:port /.\'. Leaving this field blank causes '
            'the service to use its built in default: '
            'checkip.dyndns.org:80 /.'),
    )
    ddns_checkip_path = models.CharField(
        max_length=150,
        blank=True,
        verbose_name=_('CheckIP Path'),
        help_text=_(
            'The client IP is detected by calling \'url\' from this '
            '\'ip_server_name:port /.\'. Leaving this field blank causes '
            'the service to use its built in default: '
            'checkip.dyndns.org:80 /.'),
    )
    ddns_ssl = models.BooleanField(
        verbose_name=_("Use SSL"),
    )
    ddns_custom_ddns_server = models.CharField(
        max_length=150,
        blank=True,
        verbose_name=_("Custom Server"),
        help_text=_(
            "Hostname for your custom DDNS provider."),
    )
    ddns_custom_ddns_path = models.CharField(
        max_length=150,
        blank=True,
        verbose_name=_("Custom Path"),
        help_text=_(
            "\'%h\' will be replaced with your hostname and \'%i\' will be "
            "replaced with your IP address"),
    )
    ddns_domain = models.CharField(
        max_length=120,
        blank=True,
        verbose_name=_("Domain name"),
        help_text=_("A host name alias. This option can appear multiple "
                    "times, for each domain that has the same IP. Use a comma "
                    "to separate multiple alias names."),
    )
    ddns_username = models.CharField(
        max_length=120,
        blank=True,
        verbose_name=_("Username"),
    )
    ddns_password = models.CharField(
        max_length=120,
        verbose_name=_("Password"),
    )
    ddns_period = models.IntegerField(
        verbose_name=_("Update Period"),
        help_text=_("Time in seconds."),
    )

    def __init__(self, *args, **kwargs):
        super(DynamicDNS, self).__init__(*args, **kwargs)

    class Meta:
        verbose_name = _("Dynamic DNS")
        verbose_name_plural = _("Dynamic DNS")

    class FreeAdmin:
        deletable = False
        icon_model = "DDNSIcon"


class SNMP(Model):
    snmp_location = models.CharField(
        max_length=255,
        verbose_name=_("Location"),
        blank=True,
        help_text=_("Location information, e.g. physical location of this "
                    "system: 'Floor of building, Room xyzzy'."),
    )
    snmp_contact = models.CharField(
        max_length=120,
        verbose_name=_("Contact"),
        blank=True,
        help_text=_("Contact information, e.g. name or email of the "
                    "person responsible for this system: "
                    "'admin@email.address'."),
    )
    # FIXME: Implement trap
    snmp_traps = models.BooleanField(
        verbose_name=_("Send SNMP Traps"),
        editable=False,
        default=False,
    )
    snmp_v3 = models.BooleanField(
        verbose_name=_('SNMP v3 Support'),
        default=False,
    )
    snmp_community = models.CharField(
        max_length=120,
        default='public',
        verbose_name=_("Community"),
        help_text=_("In most cases, 'public' is used here."),
        blank=True,
    )
    snmp_v3_username = models.CharField(
        blank=True,
        max_length=20,
        verbose_name=_('Username'),
    )
    snmp_v3_authtype = models.CharField(
        blank=True,
        choices=(
            ('MD5', _('MD5')),
            ('SHA', _('SHA')),
        ),
        default='SHA',
        max_length=3,
        verbose_name=_('Authentication Type'),
    )
    snmp_v3_password = models.CharField(
        blank=True,
        max_length=50,
        verbose_name=_('Password'),
    )
    snmp_v3_privproto = models.CharField(
        blank=True,
        choices=(
            ('AES', _('AES')),
            ('DES', _('DES')),
        ),
        max_length=3,
        null=True,
        verbose_name=_('Privacy Protocol'),
    )
    snmp_v3_privpassphrase = models.CharField(
        blank=True,
        max_length=100,
        null=True,
        verbose_name=_('Privacy Passphrase'),
    )
    snmp_loglevel = models.IntegerField(
        default=3,
        choices=choices.SNMP_LOGLEVEL,
        verbose_name=_('Log Level'),
    )
    snmp_options = models.TextField(
        verbose_name=_("Auxiliary parameters"),
        blank=True,
        help_text=_("These parameters will be added to /etc/snmpd.config."),
    )
    snmp_zilstat = models.BooleanField(
        verbose_name=_("Expose zilstat via SNMP"),
        default=False,
        help_text=_("Enabling this option may have performance implications on your pools."),
    )

    class Meta:
        verbose_name = _("SNMP")
        verbose_name_plural = _("SNMP")

    class FreeAdmin:
        deletable = False
        icon_model = "SNMPIcon"
        # advanced_fields = ('snmp_traps',)


class UPS(Model):
    ups_mode = models.CharField(
        default='master',
        max_length=6,
        choices=(
            ('master', _("Master")),
            ('slave', _("Slave")),
        ),
        verbose_name=_("UPS Mode"),
    )
    ups_identifier = models.CharField(
        max_length=120,
        verbose_name=_("Identifier"),
        default='ups',
        help_text=_(
            "This name is used to uniquely identify your UPS on this system."),
    )
    ups_remotehost = models.CharField(
        max_length=50,
        blank=True,
        verbose_name=_("Remote Host"),
    )
    ups_remoteport = models.IntegerField(
        default=3493,
        blank=True,
        verbose_name=_("Remote Port"),
    )
    ups_driver = models.CharField(
        max_length=120,
        verbose_name=_("Driver"),
        choices=choices.UPSDRIVER_CHOICES(),
        blank=True,
        help_text=_("The driver used to communicate with your UPS."),
    )
    ups_port = models.CharField(
        max_length=120,
        verbose_name=_("Port"),
        blank=True,
        help_text=_("The serial or USB port where your UPS is connected."),
    )
    ups_options = models.TextField(
        verbose_name=_("Auxiliary parameters (ups.conf)"),
        blank=True,
        help_text=_("Additional parameters to the hardware-specific part "
                    "of the driver."),
    )
    ups_optionsupsd = models.TextField(
        verbose_name=_("Auxiliary parameters (upsd.conf)"),
        blank=True,
        help_text=_("Additional parameters to the hardware-specific part "
                    "of the driver."),
    )
    ups_description = models.CharField(
        max_length=120,
        verbose_name=_("Description"),
        blank=True,
    )
    ups_shutdown = models.CharField(
        max_length=120,
        choices=choices.UPS_CHOICES,
        default='batt',
        verbose_name=_("Shutdown mode"),
    )
    ups_shutdowntimer = models.IntegerField(
        verbose_name=_("Shutdown timer"),
        default=30,
        help_text=_(
            "The time in seconds until shutdown is initiated. If the UPS "
            "happens to come back before the time is up the "
            "shutdown is canceled."),
    )
    ups_shutdowncmd = models.CharField(
        max_length=255,
        verbose_name=_("Shutdown Command"),
        default='/sbin/shutdown -p now',
        help_text=_(
            "The command used to shutdown the server. You can use "
            "a custom command here to perform other tasks before shutdown."
            "default: /sbin/shutdown -p now"),
    )
    ups_nocommwarntime = models.IntegerField(
        verbose_name=_('No Communication Warning Time'),
        help_text=_(
            'Notify after this many seconds if it can’t reach any of the '
            'UPS. It keeps warning you until the situation is fixed. '
            'Default is 300 seconds.'
        ),
        null=True,
        blank=True,
    )
    ups_monuser = models.CharField(
        max_length=50,
        default='upsmon',
        verbose_name=_("Monitor User")
    )
    ups_monpwd = models.CharField(
        max_length=30,
        default="fixmepass",
        verbose_name=_("Monitor Password"),
    )
    ups_extrausers = models.TextField(
        blank=True,
        verbose_name=_("Extra users (upsd.users)"),
    )
    ups_rmonitor = models.BooleanField(
        verbose_name=_("Remote Monitor"),
        default=False,
    )
    ups_emailnotify = models.BooleanField(
        verbose_name=_("Send Email Status Updates"),
        default=False,
    )
    ups_toemail = models.CharField(
        max_length=120,
        verbose_name=_("To email"),
        blank=True,
        help_text=_("Destination email address. Separate email addresses "
                    "by semi-colon."),
    )
    ups_subject = models.CharField(
        max_length=120,
        verbose_name=_("Email Subject"),
        default='UPS report generated by %h',
        help_text=_(
            "The subject of the email. You can use the following "
            "parameters for substitution:<br /><ul><li>%d - Date</li><li>"
            "%h - Hostname</li></ul>"),
    )
    ups_powerdown = models.BooleanField(
        verbose_name=_("Power Off UPS"),
        help_text=_("Signal the UPS to power off after FreeNAS shuts down."),
        default=True,
    )
    ups_hostsync = models.IntegerField(
        default=15,
        verbose_name=_("Host Sync"),
        help_text=_(
            "Upsmon will wait up to this many seconds in master mode "
            "for the slaves to disconnect during a shutdown situation"
        )
    )

    def __init__(self, *args, **kwargs):
        super(UPS, self).__init__(*args, **kwargs)
        if self.ups_monpwd:
            try:
                self.ups_monpwd = notifier().pwenc_decrypt(self.ups_monpwd)
            except Exception:
                log.debug('Failed to decrypt UPS mon password', exc_info=True)
                self.ups_monpwd = ''
        self._ups_monpwd_encrypted = False

    def save(self, *args, **kwargs):
        if self.ups_monpwd and not self._ups_monpwd_encrypted:
            self.ups_monpwd = notifier().pwenc_encrypt(self.ups_monpwd)
            self._ups_monpwd_encrypted = True
        return super(UPS, self).save(*args, **kwargs)

    class Meta:
        verbose_name = _("UPS")
        verbose_name_plural = _("UPS")

    class FreeAdmin:
        deletable = False
        icon_model = "UPSIcon"


class FTP(Model):
    ftp_port = models.PositiveIntegerField(
        default=21,
        verbose_name=_("Port"),
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        help_text=_("Port to bind FTP server."),
    )
    ftp_clients = models.PositiveIntegerField(
        default=32,
        verbose_name=_("Clients"),
        validators=[MinValueValidator(0), MaxValueValidator(10000)],
        help_text=_("Maximum number of simultaneous clients."),
    )
    ftp_ipconnections = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Connections"),
        validators=[MinValueValidator(0), MaxValueValidator(1000)],
        help_text=_("Maximum number of connections per IP address "
                    "(0 = unlimited)."),
    )
    ftp_loginattempt = models.PositiveIntegerField(
        default=3,
        verbose_name=_("Login Attempts"),
        validators=[MinValueValidator(0), MaxValueValidator(1000)],
        help_text=_("Maximum number of allowed password attempts before "
                    "disconnection."),
    )
    ftp_timeout = models.PositiveIntegerField(
        default=120,
        verbose_name=_("Timeout"),
        validators=[MinValueValidator(0), MaxValueValidator(10000)],
        help_text=_("Maximum idle time in seconds."),
    )
    ftp_rootlogin = models.BooleanField(
        verbose_name=_("Allow Root Login"),
        default=False,
    )
    ftp_onlyanonymous = models.BooleanField(
        verbose_name=_("Allow Anonymous Login"),
        default=False,
    )
    ftp_anonpath = PathField(
        blank=True,
        verbose_name=_("Path"))
    ftp_onlylocal = models.BooleanField(
        verbose_name=_("Allow Local User Login"),
        default=False,
    )
    # FIXME: rename the field
    ftp_banner = models.TextField(
        max_length=120,
        verbose_name=_("Display Login"),
        blank=True,
        help_text=_(
            "Message which will be displayed to the user when they initially "
            "login."),
    )
    ftp_filemask = models.CharField(
        max_length=3,
        default="077",
        verbose_name=_("File mask"),
        help_text=_("Override the file creation mask "
                    "(077 by default)."),
    )
    ftp_dirmask = models.CharField(
        max_length=3,
        default="077",
        verbose_name=_("Directory mask"),
        help_text=_(
            "Override the directory creation mask "
            "(077 by default)."),
    )
    ftp_fxp = models.BooleanField(
        verbose_name=_("Enable FXP"),
        default=False,
    )
    ftp_resume = models.BooleanField(
        verbose_name=_("Allow Transfer Resumption"),
        default=False,
    )
    ftp_defaultroot = models.BooleanField(
        verbose_name=_("Always Chroot"),
        help_text=_(
            "For local users, only allow access to user home directory unless "
            "the user is a member of group wheel."),
        default=False,
    )
    ftp_ident = models.BooleanField(
        verbose_name=_("Require IDENT Authentication"),
        default=False,
    )
    ftp_reversedns = models.BooleanField(
        verbose_name=_("Perform Reverse DNS Lookups"),
        default=False,
    )
    ftp_masqaddress = models.CharField(
        verbose_name=_("Masquerade address"),
        blank=True,
        max_length=120,
        help_text=_("Cause the server to display the network information "
                    "for the specified address to the client, on the "
                    "assumption that IP address or DNS host is acting as a "
                    "NAT gateway or port forwarder for the server."),
    )
    ftp_passiveportsmin = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Minimum passive port"),
        help_text=_("The minimum port to allocate for PASV style data "
                    "connections (0 = use any port)."),
    )
    ftp_passiveportsmax = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Maximum passive port"),
        help_text=_("The maximum port to allocate for PASV style data "
                    "connections (0 = use any port). Passive ports restricts "
                    "the range of ports from which the server will select "
                    "when sent the PASV command from a client. The server "
                    "will randomly " "choose a number from within the "
                    "specified range until an open" " port is found. The port "
                    "range selected must be in the " "non-privileged range "
                    "(eg. greater than or equal to 1024). It is strongly "
                    "recommended that the chosen range be large enough to "
                    "handle many simultaneous passive connections (for "
                    "example, 49152-65534, the IANA-registered ephemeral port "
                    "range)."),
    )
    ftp_localuserbw = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Local user upload bandwidth"),
        help_text=_("Local user upload bandwidth in KB/s. Zero means "
                    "infinity."),
    )
    ftp_localuserdlbw = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Local user download bandwidth"),
        help_text=_("Local user download bandwidth in KB/s. Zero means "
                    "infinity."),
    )
    ftp_anonuserbw = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Anonymous user upload bandwidth"),
        help_text=_("Anonymous user upload bandwidth in KB/s. Zero means "
                    "infinity."),
    )
    ftp_anonuserdlbw = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Anonymous user download bandwidth"),
        help_text=_("Anonymous user download bandwidth in KB/s. Zero means "
                    "infinity."),
    )
    ftp_tls = models.BooleanField(
        verbose_name=_("Enable TLS"),
        default=False,
    )
    ftp_tls_policy = models.CharField(
        max_length=120,
        choices=choices.FTP_TLS_POLICY_CHOICES,
        default="on",
        verbose_name=_("TLS policy"),
    )
    ftp_tls_opt_allow_client_renegotiations = models.BooleanField(
        verbose_name=_("TLS allow client renegotiations"),
        default=False,
    )
    ftp_tls_opt_allow_dot_login = models.BooleanField(
        verbose_name=_("TLS allow dot login"),
        default=False,
    )
    ftp_tls_opt_allow_per_user = models.BooleanField(
        verbose_name=_("TLS allow per user"),
        default=False,
    )
    ftp_tls_opt_common_name_required = models.BooleanField(
        verbose_name=_("TLS common name required"),
        default=False,
    )
    ftp_tls_opt_enable_diags = models.BooleanField(
        verbose_name=_("TLS enable diagnostics"),
        default=False,
    )
    ftp_tls_opt_export_cert_data = models.BooleanField(
        verbose_name=_("TLS export certificate data"),
        default=False,
    )
    ftp_tls_opt_no_cert_request = models.BooleanField(
        verbose_name=_("TLS no certificate request"),
        default=False,
    )
    ftp_tls_opt_no_empty_fragments = models.BooleanField(
        verbose_name=_("TLS no empty fragments"),
        default=False,
    )
    ftp_tls_opt_no_session_reuse_required = models.BooleanField(
        verbose_name=_("TLS no session reuse required"),
        default=False,
    )
    ftp_tls_opt_stdenvvars = models.BooleanField(
        verbose_name=_("TLS export standard vars"),
        default=False,
    )
    ftp_tls_opt_dns_name_required = models.BooleanField(
        verbose_name=_("TLS DNS name required"),
        default=False,
    )
    ftp_tls_opt_ip_address_required = models.BooleanField(
        verbose_name=_("TLS IP address required"),
        default=False,
    )
    ftp_ssltls_certificate = models.ForeignKey(
        Certificate,
        verbose_name=_("Certificate"),
        limit_choices_to={'cert_certificate__isnull': False, 'cert_privatekey__isnull': False},
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    ftp_options = models.TextField(
        verbose_name=_("Auxiliary parameters"),
        blank=True,
        help_text=_("These parameters are added to proftpd.conf."),
    )

    class Meta:
        verbose_name = _("FTP")
        verbose_name_plural = _("FTP")


class TFTP(Model):
    tftp_directory = PathField(
        verbose_name=_("Directory"),
        help_text=_("The directory containing the files you want to "
                    "publish. The remote host does not need to pass along the "
                    "directory as part of the transfer."),
    )
    tftp_newfiles = models.BooleanField(
        verbose_name=_("Allow New Files"),
        default=False,
    )
    tftp_host = models.CharField(
        verbose_name=_("Host"),
        max_length=120,
        default="0.0.0.0",
    )
    tftp_port = models.PositiveIntegerField(
        verbose_name=_("Port"),
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        default=69,
        help_text=_("The port to listen to. The default is to listen to "
                    "the tftp port specified in /etc/services."),
    )
    tftp_username = UserField(
        max_length=120,
        default="nobody",
        verbose_name=_("Username"),
        help_text=_("Specifies the username which the service will run "
                    "as."),
    )
    tftp_umask = models.CharField(
        max_length=120,
        verbose_name=_("umask"),
        default='022',
        help_text=_("Set the umask for newly created files to the "
                    "specified value. The default is 022 (everyone can read, "
                    "nobody can write)."),
    )
    tftp_options = models.CharField(
        max_length=120,
        verbose_name=_("Extra options"),
        blank=True,
        help_text=_("Extra command line options (usually empty)."),
    )

    class Meta:
        verbose_name = _("TFTP")
        verbose_name_plural = _("TFTP")

    class FreeAdmin:
        deletable = False
        icon_model = "TFTPIcon"


class SSH(Model):
    ssh_bindiface = MultiSelectField(
        verbose_name=_("Bind Interfaces"),
        help_text=_(
            "Interfaces to advertise and listens to. If none specified, "
            "listen for in all available addresses."
        ),
        max_length=350,
        blank=True,
        choices=choices.NICChoices(exclude_configured=False, exclude_unconfigured_vlan_parent=True),
        default='',
    )
    ssh_tcpport = models.PositiveIntegerField(
        verbose_name=_("TCP Port"),
        default=22,
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        help_text=_("Alternate TCP port. Default is 22"),
    )
    ssh_rootlogin = models.BooleanField(
        verbose_name=_("Login as Root with password"),
        help_text=_("Disabled: Root can only login via public key "
                    "authentication; Enabled: Root login permitted with "
                    "password"),
        default=False,
    )
    ssh_passwordauth = models.BooleanField(
        verbose_name=_("Allow Password Authentication"),
        default=False,
    )
    ssh_kerberosauth = models.BooleanField(
        verbose_name=_("Allow Kerberos Authentication"),
        default=False,
    )
    ssh_tcpfwd = models.BooleanField(
        verbose_name=_("Allow TCP Port Forwarding"),
        default=False,
    )
    ssh_compression = models.BooleanField(
        verbose_name=_("Compress Connections"),
        default=False,
    )
    ssh_privatekey = models.TextField(
        max_length=1024,
        verbose_name=_("Host Private Key"),
        blank=True,
        editable=False,
        help_text=_("Paste a RSA PRIVATE KEY in PEM format here."),
    )
    ssh_sftp_log_level = models.CharField(
        verbose_name=_("SFTP Log Level"),
        choices=choices.SFTP_LOG_LEVEL,
        blank=True,
        max_length=20,
        help_text=_("Specifies which messages will be logged by "
                    "sftp-server. INFO and VERBOSE log transactions that "
                    "sftp-server performs on behalf of the client. DEBUG2 and "
                    "DEBUG3 each specify higher levels of debugging output. "
                    "The default is ERROR."),
    )
    ssh_sftp_log_facility = models.CharField(
        verbose_name=_("SFTP Log Facility"),
        choices=choices.SFTP_LOG_FACILITY,
        blank=True,
        max_length=20,
        help_text=_("Specifies the facility code that is used when "
                    "logging messages from sftp-server."),
    )
    ssh_options = models.TextField(
        verbose_name=_("Extra options"),
        blank=True,
        help_text=_("Extra options to /usr/local/etc/ssh/sshd_config (usually "
                    "empty). Note, incorrect entered options prevent SSH "
                    "service to be started."),
    )
    ssh_host_dsa_key = models.TextField(
        max_length=1024,
        editable=False,
        blank=True,
        null=True,
    )
    ssh_host_dsa_key_pub = models.TextField(
        max_length=1024,
        editable=False,
        blank=True,
        null=True,
    )
    ssh_host_dsa_key_cert_pub = models.TextField(
        max_length=1024,
        blank=True,
        null=True,
        editable=False,
        verbose_name='ssh_host_dsa_key-cert.pub',
    )
    ssh_host_ecdsa_key = models.TextField(
        max_length=1024,
        editable=False,
        blank=True,
        null=True,
    )
    ssh_host_ecdsa_key_pub = models.TextField(
        max_length=1024,
        editable=False,
        blank=True,
        null=True,
    )
    ssh_host_ecdsa_key_cert_pub = models.TextField(
        max_length=1024,
        blank=True,
        null=True,
        editable=False,
        verbose_name='ssh_host_ecdsa_key-cert.pub',
    )
    ssh_host_ed25519_key_pub = models.TextField(
        max_length=1024,
        editable=False,
        blank=True,
        null=True,
    )
    ssh_host_ed25519_key = models.TextField(
        max_length=1024,
        editable=False,
        blank=True,
        null=True,
    )
    ssh_host_ed25519_key_cert_pub = models.TextField(
        max_length=1024,
        blank=True,
        null=True,
        editable=False,
        verbose_name='ssh_host_ed25519_key-cert.pub',
    )
    ssh_host_key = models.TextField(
        max_length=1024,
        editable=False,
        blank=True,
        null=True,
    )
    ssh_host_key_pub = models.TextField(
        max_length=1024,
        editable=False,
        blank=True,
        null=True,
    )
    ssh_host_rsa_key = models.TextField(
        max_length=1024,
        editable=False,
        blank=True,
        null=True,
    )
    ssh_host_rsa_key_pub = models.TextField(
        max_length=1024,
        editable=False,
        blank=True,
        null=True,
    )
    ssh_host_rsa_key_cert_pub = models.TextField(
        max_length=1024,
        blank=True,
        null=True,
        editable=False,
        verbose_name='ssh_host_rsa_key-cert.pub',
    )

    class Meta:
        verbose_name = _("SSH")
        verbose_name_plural = _("SSH")

    class FreeAdmin:
        deletable = False
        icon_model = "OpenSSHIcon"
        advanced_fields = (
            'ssh_bindiface',
            'ssh_kerberosauth',
            'ssh_sftp_log_level',
            'ssh_sftp_log_facility',
            'ssh_privatekey',
            'ssh_options',
        )


class LLDP(Model):
    lldp_intdesc = models.BooleanField(
        verbose_name=_('Interface Description'),
        default=True,
        help_text=_('Save received info in interface description / alias'),
    )
    lldp_country = models.CharField(
        verbose_name=_('Country Code'),
        max_length=2,
        help_text=_(
            'Specify a two-letterISO 3166 country code (required for LLDP'
            'location support)'),
        blank=True,
    )
    lldp_location = models.CharField(
        verbose_name=_('Location'),
        max_length=200,
        help_text=_('Specify the physical location of the host'),
        blank=True,
    )

    class Meta:
        verbose_name = _("LLDP")
        verbose_name_plural = _("LLDP")

    class FreeAdmin:
        deletable = False
        icon_model = "LLDPIcon"


class Rsyncd(Model):
    rsyncd_port = models.IntegerField(
        default=873,
        verbose_name=_("TCP Port"),
        help_text=_("Alternate TCP port. Default is 873"),
    )
    rsyncd_auxiliary = models.TextField(
        blank=True,
        verbose_name=_("Auxiliary parameters"),
        help_text=_("These parameters will be added to [global] settings "
                    "in rsyncd.conf"),
    )

    class Meta:
        verbose_name = _("Configure Rsyncd")
        verbose_name_plural = _("Configure Rsyncd")

    class FreeAdmin:
        deletable = False
        menu_child_of = "services.Rsync"
        icon_model = "rsyncdIcon"


class RsyncMod(Model):
    rsyncmod_name = models.CharField(
        max_length=120,
        verbose_name=_("Module name"),
    )
    rsyncmod_comment = models.CharField(
        max_length=120,
        blank=True,
        verbose_name=_("Comment"),
    )
    rsyncmod_path = PathField(
        verbose_name=_("Path"),
        help_text=_("Path to be shared"),
    )
    rsyncmod_mode = models.CharField(
        max_length=120,
        choices=choices.ACCESS_MODE,
        default="rw",
        verbose_name=_("Access Mode"),
        help_text=_("Control the access a remote host has to this "
                    "module"),
    )
    rsyncmod_maxconn = models.IntegerField(
        default=0,
        verbose_name=_("Maximum connections"),
        help_text=_("Maximum number of simultaneous connections. Default "
                    "is 0 (unlimited)."),
    )
    rsyncmod_user = UserField(
        max_length=120,
        default="nobody",
        verbose_name=_("User"),
        help_text=_("Specify the user name for file "
                    "transfers to and from that module. In "
                    "combination with the 'Group' option, this determines "
                    "which file permissions are available. Leave this field "
                    "empty to use default settings."),
    )
    rsyncmod_group = GroupField(
        max_length=120,
        default="nobody",
        verbose_name=_("Group"),
        help_text=_("Specify the group name for file "
                    "transfers to and from that module. "
                    "Leave this field empty to use default settings."),
    )
    rsyncmod_hostsallow = models.TextField(
        verbose_name=_("Hosts allow"),
        help_text=_("This option is a comma, space, or tab delimited set "
                    "of hosts which are permitted to access this module. Hosts "
                    "can " "be specified by name or IP address. Leave "
                    "this field empty to use default of all allowed."),
        blank=True,
    )
    rsyncmod_hostsdeny = models.TextField(
        verbose_name=_("Hosts deny"),
        help_text=_("A comma, space, or tab-delimited set "
                    "of hosts which are NOT permitted to access this module. "
                    "Where " "the lists conflict, the allow list takes "
                    "precedence. In the event that it is necessary to deny "
                    "all by default, set hosts deny to "
                    "0.0.0.0/0 and explicitly specify in the hosts "
                    "allow parameter those hosts that should be permitted "
                    "access. Leave this field empty to use the default "
                    "of none denied."),
        blank=True,
    )
    rsyncmod_auxiliary = models.TextField(
        verbose_name=_("Auxiliary parameters"),
        help_text=_("These parameters will be added to the module "
                    "configuration in rsyncd.conf."),
        blank=True,
    )

    class Meta:
        verbose_name = _("Rsync Module")
        verbose_name_plural = _("Rsync Modules")
        ordering = ["rsyncmod_name"]

    class FreeAdmin:
        menu_child_of = 'services.Rsync'
        icon_model = "rsyncModIcon"

    def __str__(self):
        return str(self.rsyncmod_name)


class SMART(Model):
    smart_interval = models.IntegerField(
        default=30,
        verbose_name=_("Check interval"),
        help_text=_("Set the interval between disk checks to N minutes. "
                    "The default is 30 minutes."),
    )
    smart_powermode = models.CharField(
        choices=choices.SMART_POWERMODE,
        default="never",
        max_length=60,
        verbose_name=_("Power mode"),
    )
    smart_difference = models.IntegerField(
        default=0,
        verbose_name=_("Difference"),
        help_text=_("Report if the temperature has changed by at least N "
                    "degrees Celsius since the last report. 0 to disable."),
    )
    smart_informational = models.IntegerField(
        default=0,
        verbose_name=_("Informational"),
        help_text=_("Report as informational in the system log if the "
                    "temperature is greater or equal than N degrees Celsius. "
                    "0 to disable."),
    )
    smart_critical = models.IntegerField(
        default=0,
        verbose_name=_("Critical"),
        help_text=_("Report as critical in the system log and send an "
                    "email if the temperature is greater or equal than N "
                    "degrees Celsius. 0 to disable."),
    )
    smart_email = models.CharField(
        verbose_name=_("Email to report"),
        max_length=255,
        blank=True,
        help_text=_("Destination email address. Separate email addresses "
                    "with spaces."),
    )

    class Meta:
        verbose_name = _("S.M.A.R.T.")
        verbose_name_plural = _("S.M.A.R.T.")

    class FreeAdmin:
        deletable = False
        icon_model = "SMARTIcon"


class RPCToken(Model):

    key = models.CharField(max_length=1024)
    secret = models.CharField(max_length=1024)

    @classmethod
    def new(cls):
        key = str(uuid.uuid4())
        h = hmac.HMAC(key=key.encode(), digestmod=hashlib.sha512)
        secret = str(h.hexdigest())
        instance = cls.objects.create(
            key=key,
            secret=secret,
        )
        return instance


class WebDAV(Model):
    webdav_protocol = models.CharField(
        max_length=120,
        choices=choices.PROTOCOL_CHOICES,
        default="http",
        verbose_name=_("Protocol"),
    )

    webdav_tcpport = models.PositiveIntegerField(
        verbose_name=_("HTTP Port"),
        default=8080,
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        help_text=_("The port on which WebDAV will run."
                    "<br>Do not use a port that is already in use by another "
                    "service (e.g. 22 for SSH)."),
    )

    webdav_tcpportssl = models.PositiveIntegerField(
        verbose_name=_("HTTPS Port"),
        default=8081,
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        help_text=_("The port on which Secure WebDAV will run."
                    "<br>Do not use a port that is already in use by another "
                    "service (e.g. 22 for SSH)."),
    )

    webdav_password = models.CharField(
        max_length=120,
        verbose_name=_("Webdav Password"),
        default="davtest",
        help_text=_("The Default Password is: davtest"),
    )

    webdav_htauth = models.CharField(
        max_length=120,
        verbose_name=_("HTTP Authentication"),
        choices=choices.HTAUTH_CHOICES,
        default='digest',
        help_text=_("Type of HTTP Authentication for WebDAV"
                    "<br>Basic Auth: Password is sent over the network as "
                    "plaintext (Avoid if HTTPS is disabled) <br>Digest Auth: "
                    "The hash of the password is sent over the network (more "
                    "secure)."),
    )

    webdav_certssl = models.ForeignKey(
        Certificate,
        verbose_name=_("Webdav SSL Certificate"),
        limit_choices_to={'cert_certificate__isnull': False, 'cert_privatekey__isnull': False},
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )

    def __init__(self, *args, **kwargs):
        super(WebDAV, self).__init__(*args, **kwargs)
        if self.webdav_password:
            try:
                self.webdav_password = notifier().pwenc_decrypt(self.webdav_password)
            except Exception:
                log.debug('Failed to decrypt Webdav password', exc_info=True)
                self.webdav_password = ''
        self._webdav_password_encrypted = False

    def save(self, *args, **kwargs):
        if self.webdav_password and not self._webdav_password_encrypted:
            self.webdav_password = notifier().pwenc_encrypt(self.webdav_password)
            self._webdav_password_encrypted = True
        return super(WebDAV, self).save(*args, **kwargs)

    class Meta:
        verbose_name = _("WebDAV")
        verbose_name_plural = _("WebDAV")

    class FreeAdmin:
        deletable = False
        icon_model = u"WebDAVShareIcon"


class S3(Model):
    s3_bindip = models.CharField(
        verbose_name=_("IP Address"),
        max_length=128,
        blank=True,
        help_text=_("Select the IP address to listen to for S3 requests. "
                    "If left unchecked, S3 will listen on all available addresses"),
    )
    s3_bindport = models.SmallIntegerField(
        verbose_name=_("Port"),
        default=9000,
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        help_text=_("TCP port on which to provide the S3 service (default 9000)"),
    )
    s3_access_key = models.CharField(
        verbose_name=_("Access key of 5 to 20 characters in length"),
        max_length=128,
        blank=True,
        default='',
        help_text=_("S3 username")
    )
    s3_secret_key = models.CharField(
        verbose_name=_("Secret key of 8 to 40 characters in length"),
        max_length=128,
        blank=True,
        default='',
        help_text=_("S3 password")
    )
    s3_browser = models.BooleanField(
        verbose_name=_("Enable Browser"),
        default=True,
        help_text=_("Enable the web user interface for the S3 service")
    )
    s3_mode = models.CharField(
        verbose_name=_("Mode"),
        max_length=120,
        choices=choices.S3_MODES,
        default="local",
        help_text=_("This doesn't do anything yet")
    )
    s3_disks = PathField(
        verbose_name=_("Disks"),
        max_length=8192,
        blank=False,
        default='',
        help_text=_("S3 filesystem directory")
    )
    s3_certificate = models.ForeignKey(
        Certificate,
        verbose_name=_("Certificate"),
        limit_choices_to={'cert_certificate__isnull': False, 'cert_privatekey__isnull': False},
        on_delete=models.SET_NULL,
        blank=True,
        null=True
    )

    class Meta:
        verbose_name = _("S3")
        verbose_name_plural = _("S3")

    class FreeAdmin:
        deletable = False
        icon_model = u"S3Icon"


class ServiceMonitor(Model):
    sm_name = models.CharField(
        verbose_name=_("Service Name"),
        max_length=120,
        unique=True
    )
    sm_host = models.CharField(
        verbose_name=_("Host Name"),
        max_length=120
    )
    sm_port = models.PositiveIntegerField(
        verbose_name=_("Port"),
        validators=[MinValueValidator(1), MaxValueValidator(65535)]
    )
    sm_frequency = models.PositiveIntegerField(
        verbose_name=_("Frequency")
    )
    sm_retry = models.PositiveIntegerField(
        verbose_name=_("Retry")
    )
    sm_enable = models.BooleanField(
        verbose_name=_("Enable"),
        default=False
    )


class NetDataGlobalSettings(Model):

    history = models.IntegerField(
        default=86400,
        null=False,
        blank=False
    )

    update_every = models.IntegerField(
        default=1,
        null=False,
        blank=False
    )

    http_port_listen_backlog = models.IntegerField(
        default=100,
        null=False,
        blank=False
    )

    bind = ListField(
        default=['0.0.0.0', '::'],
        null=False,
        blank=False,
    )

    port = models.IntegerField(
        default=19999,
        null=False,
        blank=False
    )

    additional_params = models.TextField(
        blank=True,
        null=True,
        default=''
    )

    alarms = DictField()

    stream_mode = models.CharField(
        max_length=10,
        blank=False,
        null=False,
        default='NONE'
    )

    api_key = models.CharField(
        max_length=64,
        null=True,
        blank=True
    )

    destination = ListField(
        blank=True,
        null=True
    )

    allow_from = ListField(
        default=['*'],
        null=True,
        blank=True
    )

    class Meta:
        verbose_name = _("Netdata Global Settings")

    class FreeAdmin:
        deletable = False


class Asigra(Model):
    filesystem = models.CharField(
        verbose_name=_('Base Filesystem'),
        max_length=255,
        blank=True
    )

    class Meta:
        verbose_name = _("Asigra")
        verbose_name_plural = _("Asigra")

    class FreeAdmin:
        deletable = False
        icon_model = "AsigraIcon"


class OpenVPNBase(Model):
    class Meta:
        abstract = True

    port = models.IntegerField(
        default=1194,
        verbose_name=_('Port')
    )

    protocol = models.CharField(
        max_length=4,
        default='UDP'
    )

    device_type = models.CharField(
        max_length=4,
        default='TUN'
    )

    certificate_authority = models.ForeignKey(
        CertificateAuthority,
        verbose_name=_('Certificate Authority'),
        on_delete=models.SET_NULL,
        blank=True,
        null=True
    )

    certificate = models.ForeignKey(
        Certificate,
        verbose_name=_('Certificate'),
        on_delete=models.SET_NULL,
        blank=True,
        null=True
    )

    nobind = models.BooleanField(
        default=True,
        verbose_name=_('Nobind')
    )

    authentication_algorithm = models.CharField(
        max_length=32,
        verbose_name=_('Authentication Algorithm'),
        null=True
    )

    tls_crypt_auth = models.BooleanField(
        default=True,
        verbose_name=_('TLS Crypt Authentication')
    )

    cipher = models.CharField(
        max_length=32,
        null=True
    )

    compression = models.CharField(
        max_length=32,
        null=True
    )

    additional_parameters = models.TextField(
        verbose_name=_('Additional Parameters'),
        default=''
    )


class OpenVPNServer(OpenVPNBase):
    class Meta:
        verbose_name = _('OpenVPN Server')

    class FreeAdmin:
        deletable = False

    server = models.CharField(
        verbose_name=_('Server IP'),
        max_length=45,
        default='10.8.0.0'
    )

    topology = models.CharField(
        max_length=16,
        verbose_name=_('Topology'),
        null=True
    )


class OpenVPNClient(OpenVPNBase):
    class Meta:
        verbose_name = _('OpenVPN Client')

    class FreeAdmin:
        deletable = False

    remote = models.CharField(
        verbose_name=_('Remote IP/Domain'),
        max_length=120
    )

    remote_port = models.IntegerField(
        verbose_name=_('Remote Port'),
        default=1194
    )
