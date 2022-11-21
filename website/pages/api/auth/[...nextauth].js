import NextAuth from "next-auth"
import DiscordProvider from "next-auth/providers/discord"

// noinspection JSUnresolvedVariable
export const authOptions = {
    // Configure one or more authentication providers
    providers: [
        DiscordProvider(
            {
                clientId: '1019217990111199243',
                clientSecret: 'JIgEecH6RyG9TJLZjga0EoMzafqwRIEb'
            }
        ),
        // ...add more providers here
    ],
}

export default NextAuth(authOptions);
