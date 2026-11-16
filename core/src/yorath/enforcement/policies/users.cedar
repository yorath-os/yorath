// User rights are permit. Agent and data constraints live in agents.cedar (forbid wins).

@id("owner-reads-all")
permit(
  principal is Yorath::User,
  action in [Yorath::Action::"read_doc", Yorath::Action::"query_record"],
  resource
) when { principal.role == "owner" };

@id("doc-by-role")
permit(principal is Yorath::User, action == Yorath::Action::"read_doc", resource)
when { resource.allowed_roles.contains(principal.role) };

@id("doc-by-department")
permit(principal is Yorath::User, action == Yorath::Action::"read_doc", resource)
when { resource.allowed_departments.contains(principal.department) };

@id("doc-owner")
permit(principal is Yorath::User, action == Yorath::Action::"read_doc", resource)
when { resource has owner && resource.owner == principal };

@id("employee-own-record")
permit(principal is Yorath::User, action == Yorath::Action::"query_record", resource)
when { resource has employee && resource.employee == principal };

@id("manager-direct-reports")
permit(principal is Yorath::User, action == Yorath::Action::"query_record", resource)
when { resource has manager && resource.manager == principal };

@id("hrbp-assigned-departments")
permit(principal is Yorath::User, action == Yorath::Action::"query_record", resource)
when { resource has department && principal.assigned_departments.contains(resource.department) };

@id("hr-admin-all-records")
permit(principal is Yorath::User, action == Yorath::Action::"query_record", resource)
when { principal.role == "hr_admin" };

@id("tools-for-everyone")
permit(principal, action == Yorath::Action::"use_tool", resource);
