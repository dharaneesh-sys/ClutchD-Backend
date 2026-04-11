import enum


class UserRole(str, enum.Enum):
    customer = "customer"
    mechanic = "mechanic"
    garage = "garage"
    admin = "admin"


class JobStatus(str, enum.Enum):
    idle = "idle"
    searching = "searching"
    assigned = "assigned"
    en_route = "en_route"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class RequestType(str, enum.Enum):
    auto = "auto"
    mechanic = "mechanic"
    garage = "garage"


class AssignedType(str, enum.Enum):
    mechanic = "mechanic"
    garage = "garage"


class PaymentProvider(str, enum.Enum):
    stripe = "stripe"
    razorpay = "razorpay"
    manual = "manual"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"


class DisputeStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"
