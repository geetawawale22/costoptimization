"""Commodity-scoped authority, migrated from the legacy ``Authority_Matrix.xlsx``.

Legacy did not grant a person one role. It granted a role **per commodity**: the
``User_Data`` sheet carries (NAME, ID, Commodity, User Status), and the same person appears
on several rows - one per commodity they cover. A colleague is HOD for two commodity zones
while others are PCE for a single zone.

``User.role_id`` is a single global role and cannot express that. Without this table a PCE
for one commodity carries the same authority over every other commodity's data, which is a
privilege escalation relative to the legacy system rather than a faithful migration.

``Folder_Authority`` adds the same four statuses (Superuser, HOD, Masteruser, PCE) against
a commodity folder, so the two sheets describe one model: **(user, commodity) -> role**.

The legacy workbook holds real staff names and addresses. Nothing here embeds them: rows
are created by ``tools/import_authority_matrix.py`` reading the workbook at run time.
"""
from sqlalchemy import Column, Integer, String, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.mixins import AuditMixin


class UserCommodityRole(Base, AuditMixin):
    """A role held by one user over one commodity.

    A user with no row here has no commodity-scoped authority. Whether that should fall
    back to ``User.role_id`` or deny is a policy decision - see
    ``app/services/authority_service.py``, which implements deny-by-default and explains
    why.
    """

    __tablename__ = "user_commodity_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "commodity", name="uq_user_commodity"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Stored as the legacy commodity-zone label (Trim, SheetMetal, Casting_Forging_Machining,
    # E&E, Seat_Mechanism, DAC_HVAC_Other, ...). Kept as text rather than a FK because the
    # legacy zone names are coarser than CommodityType.code and do not map one-to-one;
    # forcing a FK would require inventing a mapping. Needs Confirmation before tightening.
    commodity = Column(String(255), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    # Mirrors the legacy 'User Status' verbatim so an import round-trips without loss even
    # when a status has no CostOptima role yet.
    legacy_status = Column(String(64), nullable=True)
    status = Column(String(32), default="Active", nullable=False)
    # From the 'Persons Left' sheet. Legacy tracked departures separately rather than
    # deleting the row, so the history of who held what is preserved.
    inactive_reason = Column(String(255), nullable=True)

    user = relationship("User")
    role = relationship("Role")


class MasterSplitRule(Base, AuditMixin):
    """Which master files belong to a commodity zone - the ``Master_Split`` sheet.

    Legacy used this to decide which per-zone workbooks to merge into the consolidated
    master. CostOptima imports an already-consolidated ``Master_Rate_Data.xlsx``, so this
    is NOT wired into the import path. It is stored so the zone/file relationship is
    visible in the application and available if per-zone import is ever required.
    """

    __tablename__ = "master_split_rules"

    id = Column(Integer, primary_key=True, index=True)
    commodity = Column(String(255), nullable=False, index=True)
    file_label = Column(String(255), nullable=False)
    sheet_name = Column(String(255), nullable=True)
    unique_key = Column(String(255), nullable=True)
    header_mode = Column(String(64), nullable=True)
    folder_path = Column(String(1024), nullable=True)
