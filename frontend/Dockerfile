# Muldro frontend — Next.js app.
#
# Single stage: install all deps (the build needs devDependencies), build the
# production bundle, then serve with `next start`. BACKEND_URL is read at
# runtime by next.config (rewrites /api/* → the backend), so it is provided by
# docker-compose, not baked into the image.
FROM node:20-alpine

WORKDIR /app

# Dependency layer (cached until the lockfile changes).
COPY package.json package-lock.json ./
RUN npm ci

# App source + production build.
COPY . ./
RUN npm run build

ENV NODE_ENV=production
EXPOSE 3000
CMD ["npm", "start"]
