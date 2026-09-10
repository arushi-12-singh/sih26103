import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async redirects() {
    return [
      {
        source: "/geospatial",
        destination: "/gis-check",
        permanent: false,
      },
      {
        source: "/geospatial-view",
        destination: "/gis-check",
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
