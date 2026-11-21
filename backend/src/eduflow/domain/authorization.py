from eduflow.domain.entities import Principal
from eduflow.domain.errors import Forbidden


def require_role(actor: Principal, *roles: str) -> None:
    if actor.role not in roles:
        raise Forbidden("当前身份无权执行此操作")


def require_owner(actor: Principal, owner_id: str, organization_id: str) -> None:
    if actor.organization_id != organization_id:
        raise Forbidden("资源不属于当前机构")
    if actor.role != "ADMIN" and actor.id != owner_id:
        raise Forbidden("仅可访问本人相关资源")
