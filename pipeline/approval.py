class ApprovalPipeline:

    def finalize(self, decision):

        if decision.decision == "REJECT":
            return "REJECTED"

        if decision.decision == "REVIEW":
            return "MANUAL_REVIEW"

        return "APPROVED"