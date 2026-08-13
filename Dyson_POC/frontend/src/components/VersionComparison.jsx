import { Box, Grid, Paper, Typography, Chip, Divider } from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import PendingIcon from "@mui/icons-material/Pending";

import { STATUS_TOKENS } from "../theme";

function VersionComparison({ comparison }) {
  if (!comparison) {
    return null;
  }

  const {
    old_version,
    new_version,
    fixed = [],
    still_open = [],
    new_issues = [],
    summary = {},
  } = comparison;

  return (
    <Paper sx={{ p: 2, mb: 2 }}>
      <Typography variant="h6" fontWeight={700} sx={{ mb: 1 }}>
        Version Comparison
      </Typography>

      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Comparing Version {old_version} with Version {new_version}
      </Typography>

      {/* Summary */}
      <Grid container spacing={2}>
        {/* Fixed */}
        <Grid item xs={12} md={4}>
          <Box
            sx={{
              p: 2,
              textAlign: "center",
              borderRadius: 2,
              backgroundColor: STATUS_TOKENS.COMPLIANT.tint,
            }}
          >
            <CheckCircleIcon color="success" sx={{ fontSize: 35 }} />

            <Typography
              variant="h4"
              fontWeight={700}
              color="success.main"
            >
              {summary.fixed ?? fixed.length}
            </Typography>

            <Typography fontWeight={600}>
              Issues Fixed
            </Typography>
          </Box>
        </Grid>

        {/* Still Open */}
        <Grid item xs={12} md={4}>
          <Box
            sx={{
              p: 2,
              textAlign: "center",
              borderRadius: 2,
              backgroundColor: STATUS_TOKENS.NEEDS_REVIEW.tint,
            }}
          >
            <PendingIcon color="warning" sx={{ fontSize: 35 }} />

            <Typography
              variant="h4"
              fontWeight={700}
              color="warning.main"
            >
              {summary.still_open ?? still_open.length}
            </Typography>

            <Typography fontWeight={600}>
              Still Open
            </Typography>
          </Box>
        </Grid>

        {/* New Issues */}
        <Grid item xs={12} md={4}>
          <Box
            sx={{
              p: 2,
              textAlign: "center",
              borderRadius: 2,
              backgroundColor: STATUS_TOKENS["NON-COMPLIANT"].tint,
            }}
          >
            <ErrorIcon color="error" sx={{ fontSize: 35 }} />

            <Typography
              variant="h4"
              fontWeight={700}
              color="error.main"
            >
              {summary.new_issues ?? new_issues.length}
            </Typography>

            <Typography fontWeight={600}>
              New Issues
            </Typography>
          </Box>
        </Grid>
      </Grid>

      <Divider sx={{ my: 3 }} />

      {/* Fixed Issues */}
      <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>
        ✅ Fixed Issues
      </Typography>

      {fixed.length > 0 ? (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, mb: 3 }}>
          {fixed.map((ruleId) => (
            <Chip
              key={ruleId}
              label={ruleId}
              color="success"
              variant="outlined"
            />
          ))}
        </Box>
      ) : (
        <Typography color="text.secondary" sx={{ mb: 3 }}>
          No issues were fixed.
        </Typography>
      )}

      {/* Still Open */}
      <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>
        ⚠️ Still Open
      </Typography>

      {still_open.length > 0 ? (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1, mb: 3 }}>
          {still_open.map((ruleId) => (
            <Chip
              key={ruleId}
              label={ruleId}
              color="warning"
              variant="outlined"
            />
          ))}
        </Box>
      ) : (
        <Typography color="success.main" sx={{ mb: 3 }}>
          All previous issues have been resolved.
        </Typography>
      )}

      {/* New Issues */}
      <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>
        🔴 New Issues
      </Typography>

      {new_issues.length > 0 ? (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
          {new_issues.map((ruleId) => (
            <Chip
              key={ruleId}
              label={ruleId}
              color="error"
              variant="outlined"
            />
          ))}
        </Box>
      ) : (
        <Typography color="text.secondary">
          No new issues were introduced.
        </Typography>
      )}
    </Paper>
  );
}

export default VersionComparison;